"""
Main Agent Service - Ties all components together and runs the cognitive loop.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

from core.flow_evolution import FlowEvolution
from core.flow_manager import FlowManager, flow_manager as shared_flow_manager
from core.meta_flow import MetaFlowComposer
from core.settings import settings
from .types import (
    AgentConfig,
    Perception,
    AgentMessage,
    AgentAction,
    ActionType,
    Experience,
    FlowSpec,
    IncomingMessage,
)
from .sandbox import AgentSandbox, SandboxViolation, RateLimitExceeded
from .memory import AgentMemory
from .self_model import SelfModel
from .flow_creator import FlowCreator, node_template_types
from .flow_proposer import FlowProposer
from .communicator import AgentCommunicator


@dataclass
class AgentState:
    """Current state of the agent."""
    running: bool = False
    initialized: bool = False
    current_cycle: int = 0
    last_cycle_time: Optional[datetime] = None
    total_cycles: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)
    recent_perceptions: List[Dict[str, Any]] = field(default_factory=list)


class AgentService:
    """Main service running the autonomous agent."""
    
    def __init__(self, config: AgentConfig, flow_manager: Optional[FlowManager] = None,
                 llm: Optional[Any] = None):
        self.config = config
        self.agent_id = config.agent_id
        self.llm = llm
        self.proposer: Optional[FlowProposer] = None
        
        # Core components
        self.sandbox = AgentSandbox(config)
        self.memory = AgentMemory(self.agent_id, config.memory)
        self.self_model = SelfModel(self.agent_id, self.memory, self.sandbox, config)
        self.flow_manager = (
            flow_manager if flow_manager is not None else shared_flow_manager
        )
        self.evolution: Optional[FlowEvolution] = None
        self.flow_creator = FlowCreator(
            self.agent_id, self.sandbox, self.memory, config, self.flow_manager
        )
        self.communicator = AgentCommunicator(self.agent_id, config)
        
        # State
        self.state = AgentState()
        # NeuroCore's flow store; overridable for tests and alternate deployments.
        self.flows_store_path = Path(__file__).resolve().parents[3] / "ai_flows.json"
        
        # Set up callbacks
        self.communicator.on_perception = self._on_perception
        
        # Background tasks
        self._cognitive_task: Optional[asyncio.Task] = None
        self._queue_task: Optional[asyncio.Task] = None
        self._consolidation_task: Optional[asyncio.Task] = None
    
    async def initialize(self) -> bool:
        """Initialize the agent and all components."""
        try:
            # Attach the shared flow store so edits persist and gain version history.
            self.flow_creator.flow_manager = self.flow_manager
            self.evolution = FlowEvolution(
                self.flow_manager,
                allowed_node_types=node_template_types(),
                allowed_tools=set(
                    (self.config.sandbox or {}).get("allowed_tools", [])
                ),
            )

            # Load existing flows from NeuroCore
            await self._load_existing_flows()
            
            # Load memory from persistence
            await self._load_memory()
            
            # Initialize self-model
            # (SelfModel initializes automatically)
            
            self.state.initialized = True
            return True
            
        except Exception as e:
            self.state.errors.append({
                "timestamp": datetime.now().isoformat(),
                "phase": "initialization",
                "error": str(e)
            })
            return False
    
    async def start(self) -> None:
        """Start the agent's cognitive loop."""
        if self.state.running:
            return
        
        if not self.state.initialized and not await self.initialize():
            last = self.state.errors[-1]["error"] if self.state.errors else "unknown error"
            raise RuntimeError(f"Agent initialization failed; not starting: {last}")

        self.state.running = True
        
        # Start background tasks
        self._cognitive_task = asyncio.create_task(self._cognitive_loop())
        self._queue_task = asyncio.create_task(self._queue_processor())
        self._consolidation_task = asyncio.create_task(self._periodic_consolidation())
    
    async def stop(self) -> None:
        """Stop the agent gracefully."""
        self.state.running = False
        
        # Cancel background tasks
        for task in [self._cognitive_task, self._queue_task, self._consolidation_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    continue
        
        # Save state
        await self._save_state()
    
    async def _cognitive_loop(self) -> None:
        """Main cognitive loop - runs continuously."""
        interval = self.config.cognitive_loop.get("interval_seconds", 30)
        max_iterations = self.config.cognitive_loop.get("max_iterations_per_cycle", 3)
        
        while self.state.running:
            try:
                cycle_start = datetime.now()
                self.state.current_cycle += 1
                self.state.total_cycles += 1
                
                # 1. PERCEIVE: Get perceptions
                perceptions = await self._gather_perceptions()
                
                # 2. REFLECT: Self-reflection
                reflection = await self.self_model.reflect(perceptions[0] if perceptions else Perception(source="timer"))
                
                # 3. PLAN: Decide actions
                actions = await self._plan_actions(reflection, perceptions)
                
                # 4. EXECUTE: Run actions (limited iterations)
                for i, action in enumerate(actions[:max_iterations]):
                    try:
                        result = await self._execute_action(action)
                        await self._learn_from_action(action, result)
                    except Exception as e:
                        await self._handle_action_error(action, e)
                
                # 5. COMMUNICATE: Send pending messages
                await self.communicator.flush_queue()
                
                # 6. Record cycle
                self.state.last_cycle_time = datetime.now()
                cycle_duration = (datetime.now() - cycle_start).total_seconds()
                
                # Record performance
                self.self_model.record_execution({
                    "cycle": self.state.current_cycle,
                    "duration": cycle_duration,
                    "actions_taken": len(actions),
                    "success": True
                })
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.state.errors.append({
                    "timestamp": datetime.now().isoformat(),
                    "cycle": self.state.current_cycle,
                    "error": str(e)
                })
                # Brief pause on error
                await asyncio.sleep(5)
            
            # Wait for next cycle
            await asyncio.sleep(interval)
    
    async def _gather_perceptions(self) -> List[Perception]:
        """Gather all perceptions for this cycle."""
        perceptions = []
        
        # Get perceptions from queue
        while not self.communicator.perception_queue.empty():
            try:
                perception = self.communicator.perception_queue.get_nowait()
                perceptions.append(perception)
            except asyncio.QueueEmpty:
                break
        
        # Add timer perception if no other perceptions
        if not perceptions:
            perceptions.append(Perception(
                source="timer",
                content="Cognitive cycle timer tick"
            ))
        
        return perceptions
    
    async def _plan_actions(self, reflection: Any, perceptions: List[Perception]) -> List[AgentAction]:
        """Plan actions based on reflection and perceptions."""
        actions = []
        
        # Handle user messages
        for perception in perceptions:
            if perception.source == "user":
                actions.extend(await self._plan_for_user_message(perception))
        
        # Add recommended actions from reflection
        for rec in reflection.recommended_actions:
            actions.append(AgentAction(
                action_type=ActionType.REFLECT,
                parameters={"reflection_type": "proactive", "focus": rec},
                reasoning=f"Reflection recommendation: {rec}",
                confidence=0.7
            ))
        
        # Proactive actions based on goals
        if reflection.goal_progress:
            primary = reflection.goal_progress.get("primary", {})
            if primary.get("status") == "not_started":
                actions.append(AgentAction(
                    action_type=ActionType.CREATE_FLOW,
                    parameters={"goal": primary.get("goal")},
                    reasoning="Start working on primary goal",
                    confidence=0.8
                ))
        
        # Limit actions per cycle
        max_actions = self.config.cognitive_loop.get("max_iterations_per_cycle", 3)
        return actions[:max_actions]
    
    async def _plan_for_user_message(self, perception: Perception) -> List[AgentAction]:
        """Plan actions for a user message."""
        metadata = perception.metadata or {}
        members = metadata.get("member_flows")
        if isinstance(members, list) and members:
            parameters = {
                "name": metadata.get("name") or "Combined meta-flow",
                "member_flows": members,
            }
            if "pathways" in metadata:
                parameters["pathways"] = metadata["pathways"]
            if "metadata" in metadata:
                parameters["metadata"] = metadata["metadata"]
            if "live" in metadata:
                parameters["live"] = metadata["live"]
            return [AgentAction(
                action_type=ActionType.COMPOSE_FLOW,
                parameters=parameters,
                reasoning="Structured meta-flow composition proposal",
                confidence=0.95,
            )]

        operations = metadata.get("flow_operations")
        if metadata.get("flow_id") and isinstance(operations, list) and operations:
            parameters: Dict[str, Any] = {
                "flow_id": metadata["flow_id"],
                "operations": operations,
            }
            if "evaluation_input" in metadata:
                parameters["evaluation_input"] = metadata["evaluation_input"]
            if "auto_revert" in metadata:
                parameters["auto_revert"] = metadata["auto_revert"]
            return [AgentAction(
                action_type=ActionType.UPDATE_FLOW,
                parameters=parameters,
                reasoning=f"Structured evolution proposal for {metadata['flow_id']}",
                confidence=0.95,
            )]

        actions = []
        content = perception.content.lower()
        flow_id = metadata.get("flow_id") or self._extract_flow_id(perception.content)
        member_flows = self._extract_flow_ids(perception.content)

        # Determine intent
        if any(kw in content for kw in ["combine", "compose", "merge"]) and len(member_flows) >= 2:
            parameters = {
                "name": metadata.get("name") or "Combined meta-flow",
                "member_flows": member_flows,
            }
            if "pathways" in metadata:
                parameters["pathways"] = metadata["pathways"]
            if "metadata" in metadata:
                parameters["metadata"] = metadata["metadata"]
            if "live" in metadata:
                parameters["live"] = metadata["live"]
            elif "live" in content:
                parameters["live"] = True
            actions.append(AgentAction(
                action_type=ActionType.COMPOSE_FLOW,
                parameters=parameters,
                reasoning="User requested a meta-flow composition",
                confidence=0.8,
            ))
        elif any(kw in content for kw in ["evolve", "improve flow", "optimize flow",
                                          "edit flow"]) and flow_id:
            parameters = {"flow_id": flow_id, "instruction": perception.content}
            if "evaluation_input" in metadata:
                parameters["evaluation_input"] = metadata["evaluation_input"]
            if "auto_revert" in metadata:
                parameters["auto_revert"] = metadata["auto_revert"]
            actions.append(AgentAction(
                action_type=ActionType.UPDATE_FLOW,
                parameters=parameters,
                reasoning=f"User requested flow evolution for {flow_id}",
                confidence=0.75,
            ))
        elif any(kw in content for kw in ["create", "make", "build", "new flow"]):
            # Extract flow specification
            spec = await self._extract_flow_spec(perception.content)
            if spec:
                actions.append(AgentAction(
                    action_type=ActionType.CREATE_FLOW,
                    parameters=spec.to_dict(),
                    reasoning=f"User requested flow creation: {perception.content[:100]}",
                    confidence=0.9
                ))
        
        elif any(kw in content for kw in ["run", "execute", "start"]):
            # Execute existing flow
            flow_id = self._extract_flow_id(perception.content)
            if flow_id:
                actions.append(AgentAction(
                    action_type=ActionType.EXECUTE_FLOW,
                    parameters={"flow_id": flow_id},
                    reasoning=f"User requested flow execution: {flow_id}",
                    confidence=0.8
                ))
        
        elif any(kw in content for kw in ["remember", "save", "store"]):
            # Store memory
            actions.append(AgentAction(
                action_type=ActionType.REMEMBER,
                parameters={"content": perception.content, "context": "user_request"},
                reasoning="User asked to remember something",
                confidence=0.8
            ))
        
        elif any(kw in content for kw in ["recall", "what", "tell me", "show me"]):
            # Recall memory
            actions.append(AgentAction(
                action_type=ActionType.RECALL,
                parameters={"query": perception.content, "limit": 5},
                reasoning="User asked for information recall",
                confidence=0.8
            ))
        
        elif any(kw in content for kw in ["reflect", "how are you", "status", "self"]):
            # Self-reflection
            actions.append(AgentAction(
                action_type=ActionType.INSPECT_SELF,
                parameters={"fields": ["flows", "memory_stats", "performance"]},
                reasoning="User requested self-reflection/status",
                confidence=0.9
            ))
        
        else:
            # Default: chat response
            actions.append(AgentAction(
                action_type=ActionType.EXECUTE_FLOW,
                parameters={"flow_type": "chat", "input": perception.content},
                reasoning="General chat response",
                confidence=0.6
            ))
        
        return actions
    
    async def _extract_flow_spec(self, content: str) -> Optional[FlowSpec]:
        """Extract flow specification from user message."""
        # Simplified extraction (in production, use LLM)
        return FlowSpec(
            description=content,
            required_nodes=["chat_input", "llm_module", "chat_output"],
            trigger="manual"
        )
    
    def _extract_flow_id(self, content: str) -> Optional[str]:
        """Extract flow ID from user message."""
        import re
        match = re.search(r'(agent_[a-z0-9_-]+|[a-f0-9]{8,})', content)
        return match.group(1) if match else None

    def _extract_flow_ids(self, content: str) -> List[str]:
        """Extract unique flow IDs in appearance order."""
        import re
        matches = re.findall(r'(agent_[a-z0-9_-]+|[a-f0-9]{8,})', content)
        seen = set()
        result = []
        for match in matches:
            if match not in seen:
                seen.add(match)
                result.append(match)
        return result
    
    # Action types with a real handler below. Anything else is rejected rather than reported as
    # done: the sandbox only authorizes and returns a "delegated" placeholder.
    SUPPORTED_ACTIONS = frozenset({
        ActionType.COMPOSE_FLOW, ActionType.CREATE_FLOW, ActionType.UPDATE_FLOW,
        ActionType.EXECUTE_FLOW, ActionType.REMEMBER, ActionType.RECALL,
        ActionType.INSPECT_SELF, ActionType.SEND_MESSAGE,
    })

    async def _execute_action(self, action: AgentAction) -> Dict[str, Any]:
        """Execute an action through the sandbox."""
        if action.action_type not in self.SUPPORTED_ACTIONS:
            return {
                "success": False,
                "error": f"Action '{action.action_type.value}' is not supported by this agent",
            }
        try:
            result = await self.sandbox.execute_action(action)
            
            # Handle specific action types
            if action.action_type == ActionType.COMPOSE_FLOW:
                result = await self._execute_compose_flow(action, result)
            elif action.action_type == ActionType.CREATE_FLOW:
                result = await self._execute_create_flow(action, result)
            elif action.action_type == ActionType.UPDATE_FLOW:
                result = await self._execute_update_flow(action, result)
            elif action.action_type == ActionType.EXECUTE_FLOW:
                result = await self._execute_flow(action, result)
            elif action.action_type == ActionType.REMEMBER:
                result = await self._execute_remember(action, result)
            elif action.action_type == ActionType.RECALL:
                result = await self._execute_recall(action, result)
            elif action.action_type == ActionType.INSPECT_SELF:
                result = await self._execute_inspect_self(action, result)
            elif action.action_type == ActionType.SEND_MESSAGE:
                result = await self._execute_send_message(action, result)
            
            return {"success": True, "result": result}
            
        except SandboxViolation as e:
            return {"success": False, "error": f"Sandbox violation: {e}"}
        except RateLimitExceeded as e:
            return {"success": False, "error": f"Rate limited: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _execute_create_flow(self, action: AgentAction, sandbox_result: Dict) -> Dict[str, Any]:
        """Execute flow creation."""
        spec = FlowSpec(**action.parameters)
        flow = await self.flow_creator.create_flow(spec)
        return {"flow_id": flow["id"], "flow": flow}
    
    async def _execute_compose_flow(self, action: AgentAction,
                                    sandbox_result: Dict) -> Dict[str, Any]:
        """Compose owned member flows into a meta-flow."""
        name = action.parameters.get("name") or "Combined meta-flow"
        live = bool(action.parameters.get("live", False))
        meta = await self.compose_meta_flow(
            name,
            action.parameters.get("member_flows", []),
            action.parameters.get("pathways"),
            action.parameters.get("metadata"),
            live=live,
        )
        return {"flow_id": meta["id"], "flow": meta}

    async def _execute_update_flow(self, action: AgentAction, sandbox_result: Dict) -> Dict[str, Any]:
        """Apply a self-evolution proposal to a flow the agent owns."""
        flow_id = action.parameters.get("flow_id")
        operations = action.parameters.get("operations") or []
        instruction = action.parameters.get("instruction")
        if not operations and instruction:
            result = await self.propose_and_evolve(
                flow_id,
                instruction,
                action.parameters.get("evaluation_input"),
                action.parameters.get("auto_revert", True),
            )
            outcome = result["outcome"]
            return {
                "flow_id": outcome["flow"]["id"],
                "operations": result["operations"],
                **outcome,
            }
        if "evaluation_input" in action.parameters:
            outcome = await self.evolve_and_evaluate(
                flow_id,
                operations,
                action.parameters.get("evaluation_input"),
                auto_revert=action.parameters.get("auto_revert", True),
            )
            return {"flow_id": outcome["flow"]["id"], **outcome}
        flow = await self.evolve_flow(flow_id, operations)
        return {"flow_id": flow["id"], "flow": flow}

    async def evolve_flow(self, flow_id: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Validated, versioned self-edit of an owned flow."""
        if not self.sandbox.check_flow_ownership(flow_id):
            raise SandboxViolation("Cannot evolve flow not owned by agent")
        if self.evolution is None:
            raise RuntimeError("agent is not initialized")
        flow = self.evolution.mutate(flow_id, operations)
        self.flow_creator.created_flows[flow_id] = flow
        await self.memory.store_experience(Experience(
            type="flow_evolution",
            action="update_flow",
            result={"flow_id": flow_id, "operations": operations},
        ))
        return flow

    @staticmethod
    def _evaluation_succeeded(result: Any) -> bool:
        if result is None:
            return False
        if isinstance(result, dict):
            if result.get("error"):
                return False
            if str(result.get("status", "")).lower() in {"error", "failed"}:
                return False
        return True

    async def evolve_and_evaluate(self, flow_id: str, operations: List[Dict[str, Any]],
                                  evaluation_input: Dict[str, Any],
                                  auto_revert: bool = True,
                                  evaluator: Optional[Any] = None) -> Dict[str, Any]:
        """Apply an edit, run it, and revert when the evaluation fails."""
        if not self.sandbox.check_flow_ownership(flow_id):
            raise SandboxViolation("Cannot evolve flow not owned by agent")
        if self.evolution is None:
            raise RuntimeError("agent is not initialized")

        flow = self.evolution.mutate(flow_id, operations)
        self.flow_creator.created_flows[flow_id] = flow
        versions = self.evolution.history(flow_id)
        pre_version = versions[0]["version"] if versions else None

        try:
            evaluation = await self.execute_flow(flow_id, evaluation_input)
            succeeded = (
                evaluator(evaluation) if evaluator is not None
                else self._evaluation_succeeded(evaluation)
            )
        except Exception as exc:
            evaluation = {"error": str(exc)}
            succeeded = False

        if not succeeded and auto_revert and pre_version is not None:
            restored = self.evolution.rollback(flow_id, pre_version)
            self.flow_creator.created_flows[flow_id] = restored
            await self.memory.store_experience(Experience(
                type="flow_evolution",
                action="evaluate_flow",
                result={"flow_id": flow_id, "reverted": True,
                        "evaluation": evaluation},
            ))
            return {"flow": restored, "reverted": True, "evaluation": evaluation}

        await self.memory.store_experience(Experience(
            type="flow_evolution",
            action="evaluate_flow",
            result={"flow_id": flow_id, "reverted": False,
                    "evaluation": evaluation},
        ))
        return {"flow": flow, "reverted": False, "evaluation": evaluation}

    def _get_proposer(self) -> FlowProposer:
        if self.proposer is not None:
            return self.proposer
        if self.llm is None:
            from core.llm import LLMBridge
            self.llm = LLMBridge(
                base_url=settings.get("llm_api_url"),
                api_key=settings.get("llm_api_key") or None,
            )
        self.proposer = FlowProposer(
            self.llm,
            allowed_node_types=node_template_types(),
            allowed_tools=set(
                (self.config.sandbox or {}).get("allowed_tools", [])
            ),
        )
        return self.proposer

    async def propose_evolution(self, flow_id: str, goal: str) -> List[Dict[str, Any]]:
        """Ask the model for a validated-shape edit proposal (no write)."""
        if not self.sandbox.check_flow_ownership(flow_id):
            raise SandboxViolation("Cannot propose edits for a flow not owned by agent")
        flow = self.flow_manager.get_flow(flow_id)
        if flow is None:
            raise ValueError(f"Flow '{flow_id}' does not exist")
        return await self._get_proposer().propose(flow, goal)

    async def propose_and_evolve(self, flow_id: str, instruction: str,
                                 evaluation_input: Optional[Dict[str, Any]] = None,
                                 auto_revert: bool = True) -> Dict[str, Any]:
        """Generate operations, then apply them (with evaluation when provided)."""
        operations = await self.propose_evolution(flow_id, instruction)
        if evaluation_input is None:
            flow = await self.evolve_flow(flow_id, operations)
            return {
                "operations": operations,
                "outcome": {"flow": flow, "reverted": False},
            }
        outcome = await self.evolve_and_evaluate(
            flow_id, operations, evaluation_input, auto_revert
        )
        return {"operations": operations, "outcome": outcome}

    async def compose_meta_flow(self, name: str, member_flow_ids: List[str],
                                pathways: Optional[List[Dict[str, Any]]] = None,
                                metadata: Optional[Dict[str, Any]] = None,
                                live: bool = False) -> Dict[str, Any]:
        """Combine owned flows (ganglia) into one saved meta-flow."""
        for member_id in member_flow_ids:
            if not self.sandbox.check_flow_ownership(member_id):
                raise SandboxViolation(
                    "Cannot compose a flow not owned by agent"
                )
        combined_metadata = {
            "creator": self.agent_id,
            "created_at": datetime.now().isoformat(),
        }
        if metadata:
            combined_metadata.update(metadata)
        composer = MetaFlowComposer(self.flow_manager)
        if live:
            meta = composer.compose_live(
                name, member_flow_ids, pathways=pathways,
                metadata=combined_metadata,
            )
        else:
            meta = composer.compose(
                name, member_flow_ids, pathways=pathways,
                metadata=combined_metadata,
            )
        self.sandbox.register_flow_ownership(meta["id"])
        self.flow_creator.created_flows[meta["id"]] = meta
        await self.memory.store_experience(Experience(
            type="flow_evolution",
            action="compose_meta_flow",
            result={"flow_id": meta["id"], "members": list(member_flow_ids)},
        ))
        return meta

    async def rollback_flow(self, flow_id: str, version: int) -> Dict[str, Any]:
        """Restore an owned flow to a version and refresh the agent cache."""
        if not self.sandbox.check_flow_ownership(flow_id):
            raise SandboxViolation("Cannot roll back flow not owned by agent")
        if self.evolution is None:
            raise RuntimeError("agent is not initialized")
        flow = self.evolution.rollback(flow_id, version)
        self.flow_creator.created_flows[flow_id] = flow
        await self.memory.store_experience(Experience(
            type="flow_evolution",
            action="rollback_flow",
            result={"flow_id": flow_id, "version": version},
        ))
        return flow

    def _resolve_active_flow(self) -> str:
        """First active flow that still exists, else the default chat flow."""
        active = settings.get("active_ai_flows", []) or []
        for flow_id in active:
            if self.flow_manager.get_flow(flow_id) is not None:
                return flow_id
        if self.flow_manager.get_flow("default-flow-001") is not None:
            return "default-flow-001"
        raise ValueError("no active flow is available")

    async def _execute_flow(self, action: AgentAction, sandbox_result: Dict) -> Dict[str, Any]:
        """Execute the requested flow, falling back to the active chat flow."""
        requested = action.parameters.get("flow_id")
        if requested:
            active = settings.get("active_ai_flows", []) or []
            if (not self.sandbox.check_flow_ownership(requested)
                    and requested not in active
                    and requested != "default-flow-001"):
                raise SandboxViolation("Cannot execute flow not owned by agent")
            flow_id = requested
        else:
            flow_id = self._resolve_active_flow()
        result = await self._run_flow(
            flow_id,
            action.parameters.get("input", {}),
            action.parameters.get("timeout"),
        )
        return {"flow_id": flow_id, "status": "completed", "result": result}
    
    async def _execute_remember(self, action: AgentAction, sandbox_result: Dict) -> Dict[str, Any]:
        """Execute memory storage."""
        content = action.parameters.get("content", "")
        context = action.parameters.get("context", "user_request")
        
        experience = Experience(
            type="memory_storage",
            perception=None,
            action="remember",
            result={"content": content, "context": context}
        )
        
        await self.memory.store_experience(experience)
        return {"stored": True, "content_length": len(content)}
    
    async def _execute_recall(self, action: AgentAction, sandbox_result: Dict) -> Dict[str, Any]:
        """Execute memory recall."""
        query = action.parameters.get("query", "")
        limit = action.parameters.get("limit", 5)
        
        # Use memory system
        context = {"query": query, "tags": []}
        memories = await self.memory.recall_relevant(context, limit)
        
        return {
            "memories": [
                {
                    "id": m.id,
                    "type": m.type,
                    "content": m.content,
                    "importance": m.importance,
                    "created_at": m.created_at.isoformat()
                }
                for m in memories
            ]
        }
    
    async def _execute_inspect_self(self, action: AgentAction, sandbox_result: Dict) -> Dict[str, Any]:
        """Execute self-inspection."""
        fields = action.parameters.get("fields", ["flows", "memory_stats", "performance"])
        return self.self_model.get_self_summary()
    
    async def _execute_send_message(self, action: AgentAction, sandbox_result: Dict) -> Dict[str, Any]:
        """Execute sending a message."""
        message = AgentMessage(
            channel=action.parameters.get("channel", "chat"),
            content=action.parameters.get("content", ""),
            message_type=action.parameters.get("type", "response"),
            priority=action.parameters.get("priority", "normal"),
            metadata=action.parameters.get("metadata", {})
        )
        
        result = await self.communicator.send_message(message)
        return {"queued": True, "result": result}
    
    async def _learn_from_action(self, action: AgentAction, result: Dict[str, Any]):
        """Learn from action execution."""
        experience = Experience(
            type=f"action_{action.action_type.value}",
            perception=None,
            action=action.action_type.value,
            result=result,
            context={"reasoning": action.reasoning, "confidence": action.confidence}
        )
        
        await self.memory.store_experience(experience)
        
        # Record in self-model for performance tracking
        if result.get("success"):
            self.self_model.record_execution({
                "action": action.action_type.value,
                "success": True,
                "timestamp": datetime.now().isoformat()
            })
    
    async def _handle_action_error(self, action: AgentAction, error: Exception):
        """Handle action execution error."""
        error_record = {
            "timestamp": datetime.now().isoformat(),
            "action": action.action_type.value,
            "error": str(error),
            "reasoning": action.reasoning
        }
        
        self.state.errors.append(error_record)
        
        # Learn from error
        experience = Experience(
            type="action_error",
            perception=None,
            action=action.action_type.value,
            result={"error": str(error)},
            context={"reasoning": action.reasoning}
        )
        await self.memory.store_experience(experience)
    
    async def _queue_processor(self):
        """Background task to process message queues."""
        while self.state.running:
            try:
                await self.communicator.flush_queue()
                await asyncio.sleep(5)  # Process every 5 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.state.errors.append({
                    "timestamp": datetime.now().isoformat(),
                    "task": "queue_processor",
                    "error": str(e)
                })
                await asyncio.sleep(10)
    
    async def _periodic_consolidation(self):
        """Periodic memory consolidation."""
        interval_hours = self.config.memory.get("semantic_consolidation_interval_hours", 24)
        interval_seconds = interval_hours * 3600
        
        while self.state.running:
            try:
                await asyncio.sleep(interval_seconds)
                if self.state.running:
                    await self.memory.consolidate()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.state.errors.append({
                    "timestamp": datetime.now().isoformat(),
                    "task": "consolidation",
                    "error": str(e)
                })
    
    async def _on_perception(self, perception: Perception):
        """Record a perception so the next cycle can inspect it."""
        self.state.recent_perceptions.append({
            "source": perception.source,
            "channel": perception.channel,
            "content": perception.content,
            "metadata": perception.metadata,
            "timestamp": perception.timestamp.isoformat(),
        })
        if len(self.state.recent_perceptions) > 50:
            self.state.recent_perceptions = self.state.recent_perceptions[-50:]

    def _state_path(self) -> Optional[Path]:
        """Resolve the configured persistence path, if persistence is enabled."""
        persistence = self.config.persistence
        if not persistence.get("enabled", True):
            return None
        configured = persistence.get(
            "state_path", "modules/autonomous_agent/data/agent_state.json"
        )
        path = Path(configured)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[3] / path
        return path

    async def _load_existing_flows(self):
        """Adopt flows this agent created from the shared flow store."""
        flows = self.flow_manager.get_all_flows_dict()
        for flow_id, flow in flows.items():
            if not isinstance(flow, dict):
                continue
            metadata = flow.get("metadata")
            if not isinstance(metadata, dict):
                continue
            if metadata.get("creator") != self.agent_id:
                continue
            self.flow_creator.created_flows[flow_id] = flow
            self.sandbox.register_flow_ownership(flow_id)

    async def _load_memory(self):
        """Restore memory and aggregate counters from the persistence file."""
        path = self._state_path()
        if path is None or not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                snapshot = json.load(handle)
            if not isinstance(snapshot, dict):
                raise ValueError("state snapshot must be a JSON object")
            memory_snapshot = snapshot.get("memory", {})
            if not isinstance(memory_snapshot, dict):
                raise ValueError("state memory must be a JSON object")
            owned_flows = snapshot.get("owned_flows", [])
            if not isinstance(owned_flows, list):
                raise ValueError("state owned_flows must be a JSON array")
            snapshot_errors = snapshot.get("errors", [])
            if not isinstance(snapshot_errors, list):
                raise ValueError("state errors must be a JSON array")
            self.memory.load_dict(memory_snapshot)
            self.state.current_cycle = int(snapshot.get("current_cycle", 0))
            self.state.total_cycles = int(snapshot.get("total_cycles", 0))
            self.state.errors = list(snapshot_errors)[-100:]
            for flow_id in owned_flows:
                if isinstance(flow_id, str):
                    self.sandbox.register_flow_ownership(flow_id)
        except (OSError, ValueError, TypeError) as exc:
            self.state.errors.append({
                "timestamp": datetime.now().isoformat(),
                "phase": "memory_loading",
                "error": str(exc),
            })
            quarantine = path.with_name(
                f"{path.name}.corrupt-{int(datetime.now().timestamp())}"
            )
            try:
                os.replace(path, quarantine)
            except OSError as quarantine_error:
                self.state.errors.append({
                    "timestamp": datetime.now().isoformat(),
                    "phase": "memory_quarantine",
                    "error": str(quarantine_error),
                })

    async def _save_state(self):
        """Persist memory and cycle statistics atomically."""
        path = self._state_path()
        if path is None:
            return
        snapshot = {
            "agent_id": self.agent_id,
            "saved_at": datetime.now().isoformat(),
            "current_cycle": self.state.current_cycle,
            "total_cycles": self.state.total_cycles,
            "errors": self.state.errors[-100:],
            "owned_flows": self.sandbox.get_owned_flows(),
            "memory": self.memory.to_dict(),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_name(f"{path.name}.tmp")
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except OSError as exc:
            self.state.errors.append({
                "timestamp": datetime.now().isoformat(),
                "phase": "state_saving",
                "error": str(exc),
            })
    
    # Public API methods for external control
    
    async def create_flow(self, spec: FlowSpec) -> Dict[str, Any]:
        """Public API to create a flow."""
        return await self.flow_creator.create_flow(spec)
    
    async def execute_flow(self, flow_id: str, input_data: Dict[str, Any],
                           timeout: Optional[float] = None) -> Dict[str, Any]:
        """Execute an owned flow through the real FlowRunner."""
        if not self.sandbox.check_flow_ownership(flow_id):
            raise SandboxViolation("Cannot execute flow not owned by agent")
        return await self._run_flow(flow_id, input_data, timeout)

    async def _run_flow(self, flow_id: str, input_data: Dict[str, Any],
                        timeout: Optional[float] = None) -> Dict[str, Any]:
        """Run any stored flow; ownership is enforced by the public/caller path."""
        flow = self.flow_manager.get_flow(flow_id)
        if flow is None:
            raise ValueError(f"Flow '{flow_id}' does not exist")

        from core.flow_runner import FlowRunner
        from modules.meta.node import _SUBFLOW_MANAGER

        runner = FlowRunner(flow_id, flow_override=flow)
        token = _SUBFLOW_MANAGER.set(self.flow_manager)
        try:
            return await runner.run(input_data or {}, timeout=timeout)
        finally:
            _SUBFLOW_MANAGER.reset(token)

    def activate_flow(self, flow_id: str) -> List[str]:
        """Make an owned flow the first active flow for the app chat gateway."""
        if not self.sandbox.check_flow_ownership(flow_id):
            raise SandboxViolation("Cannot activate flow not owned by agent")
        if self.flow_manager.get_flow(flow_id) is None:
            raise ValueError(f"Flow '{flow_id}' does not exist")
        active = list(settings.get("active_ai_flows", []) or [])
        if flow_id in active:
            active.remove(flow_id)
        active.insert(0, flow_id)
        settings.save_settings({"active_ai_flows": active})
        return active
    
    async def send_message(self, message: AgentMessage) -> Dict[str, Any]:
        """Public API to send a message."""
        return await self.communicator.send_message(message)
    
    async def notify_user(self, content: str, channel: str = None, priority: str = "normal") -> Dict[str, Any]:
        """Public API to notify user."""
        return await self.communicator.notify_user(content, channel, priority)
    
    async def get_self_status(self) -> Dict[str, Any]:
        """Get comprehensive agent status."""
        return {
            "agent_id": self.agent_id,
            "name": self.config.name,
            "state": {
                "running": self.state.running,
                "initialized": self.state.initialized,
                "current_cycle": self.state.current_cycle,
                "total_cycles": self.state.total_cycles,
                "last_cycle": self.state.last_cycle_time.isoformat() if self.state.last_cycle_time else None,
                "errors_count": len(self.state.errors)
            },
            "sandbox": {
                "owned_flows": len(self.sandbox.get_owned_flows()),
                "resource_usage": self.sandbox.get_resource_usage()
            },
            "memory": self.memory.get_all_stats(),
            "self_model": self.self_model.get_self_summary(),
            "communication": {
                "pending_messages": self.communicator.get_pending_outgoing(),
                "recent_log": self.communicator.get_communication_log(10)
            }
        }
    
    async def handle_user_message(self, channel: str, content: str, user_id: str, metadata: Dict = None) -> Perception:
        """Handle incoming user message."""
        incoming = IncomingMessage(
            channel=channel,
            content=content,
            user_id=user_id,
            metadata=metadata or {}
        )
        return await self.communicator.handle_incoming(incoming)


def create_agent_service(config_dict: Dict[str, Any]) -> AgentService:
    """Factory function to create agent service from config dict."""
    config = AgentConfig(**config_dict)
    return AgentService(config)