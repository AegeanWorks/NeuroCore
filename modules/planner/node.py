import logging
import json
import re
from collections import deque
from core.llm import LLMBridge
from core.settings import settings
from core.flow_data import (
    FlowData,
    get_messages,
    get_reasoning_context,
    set_plan,
    set_current_step,
    set_original_request,
    set_plan_needed,
    set_plan_context,
    set_plan_complete,
    set_step_completed,
    set_completed_steps,
    set_dependency_error,
    set_next_step,
    set_planning_error,
    ensure_flow_data,
)
from core.session_manager import get_session_manager, EpisodeState

logger = logging.getLogger(__name__)


class PlannerExecutor:
    def __init__(self):
        self.llm = LLMBridge(
            base_url=settings.get("llm_api_url"),
            api_key=settings.get("llm_api_key")
        )
        # Lazy import to avoid circular dependencies
        self._reasoning_service = None
    
    # ========== Helper Methods for auto_track mode ==========
    
    def _generate_plan_context(self, plan: list, current_step: int, completed_steps: set) -> str:
        """Generate plan_context string showing progress (used in auto_track mode)."""
        if not plan:
            return ""
        
        plan_lines = []
        for i, p in enumerate(plan):
            step_num = p.get('step', i + 1)
            action = p['action']
            target = p.get('target', '')
            
            # Mark completed steps, current step, and pending steps
            if i in completed_steps:
                plan_lines.append(f"✓ {step_num}. {action}: {target} (COMPLETED)")
            elif i == current_step:
                plan_lines.append(f"→ {step_num}. {action}: {target} (CURRENT)")
            else:
                plan_lines.append(f"  {step_num}. {action}: {target}")
        
        plan_text = "\n".join(plan_lines)
        return f"## Execution Plan\nCurrently on step {current_step + 1} of {len(plan)}.\n{plan_text}"
    
    def _build_dependency_graph(self, plan: list) -> tuple:
        """Build adjacency list and in-degree count for topological sort."""
        n = len(plan)
        adj = {i: [] for i in range(n)}  # step_index -> [dependent_step_indices]
        in_degree = {i: 0 for i in range(n)}  # step_index -> number of dependencies
        
        # Map step numbers to indices for dependency resolution
        step_num_to_idx = {}
        for i, step in enumerate(plan):
            step_num = step.get('step', i + 1)
            step_num_to_idx[step_num] = i
        
        for i, step in enumerate(plan):
            depends_on = step.get('depends_on')
            if depends_on is not None:
                # Handle single dependency or list of dependencies
                if isinstance(depends_on, int):
                    deps = [depends_on]
                elif isinstance(depends_on, list):
                    deps = depends_on
                else:
                    deps = []
                
                for dep_step_num in deps:
                    # Find the index of the dependency step
                    dep_idx = step_num_to_idx.get(dep_step_num)
                    if dep_idx is not None and dep_idx < len(plan):
                        # Add edge: dep_idx -> i (i depends on dep_idx)
                        adj[dep_idx].append(i)
                        in_degree[i] += 1
        
        return adj, in_degree
    
    def _detect_circular_dependencies(self, plan: list) -> tuple:
        """Detect circular dependencies using Kahn's algorithm."""
        if not plan:
            return False, None
        
        adj, in_degree = self._build_dependency_graph(plan)
        n = len(plan)
        
        # Find all steps with no dependencies
        queue = deque([i for i in range(n) if in_degree[i] == 0])
        visited_count = 0
        visited_order = []
        
        while queue:
            u = queue.popleft()
            visited_order.append(u)
            visited_count += 1
            
            for v in adj[u]:
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)
        
        if visited_count != n:
            # Circular dependency detected
            unvisited = [i for i in range(n) if i not in visited_order]
            return True, unvisited
        
        return False, None
    
    def _get_executable_steps(self, plan: list, completed_steps: set) -> list:
        """Get list of step indices that can be executed (all dependencies satisfied)."""
        if not plan:
            return []

        n = len(plan)
        # Build once — reused for every step's dependency check (O(n) not O(n²)).
        step_num_to_idx = {plan[j].get('step', j + 1): j for j in range(n)}

        # Calculate which steps have all dependencies completed
        executable = []
        for i in range(n):
            if i in completed_steps:
                continue  # Already done

            # Check if all dependencies are completed
            deps_satisfied = True
            step = plan[i]
            depends_on = step.get('depends_on')

            if depends_on is not None:
                if isinstance(depends_on, int):
                    deps = [depends_on]
                elif isinstance(depends_on, list):
                    deps = depends_on
                else:
                    deps = []

                for dep_step_num in deps:
                    dep_idx = step_num_to_idx.get(dep_step_num)
                    if dep_idx is not None and dep_idx not in completed_steps:
                        deps_satisfied = False
                        break

            if deps_satisfied:
                executable.append(i)

        return executable

    def _run_auto_track(self, data: dict) -> dict:
        """
        Run one progression tick (tracker behavior) when auto_track is enabled.
        This performs: complete current step, compute next step, update context.
        """
        plan = data.get("plan", [])
        current = data.get("current_step", 0)
        completed_steps = set(data.get("completed_steps", []))
        
        # If no plan, nothing to track
        if not plan:
            return data
        
        result = data.copy()
        
        # Check for circular dependencies
        has_cycle, cycle_steps = self._detect_circular_dependencies(plan)
        if has_cycle:
            result["dependency_error"] = f"Circular dependency detected in steps: {[plan[i].get('step', i+1) for i in cycle_steps]}"
            result["plan_complete"] = True  # Stop execution
            return result
        
        # Resume logic: if current_step > 0, assume prior steps completed
        if current > 0:
            for i in range(current):
                if i < len(plan):
                    completed_steps.add(i)
        
        # Mark current step as completed (if valid)
        step_completed_now = False
        if 0 <= current < len(plan):
            if current not in completed_steps:
                completed_steps.add(current)
                result["step_completed"] = plan[current]
                step_completed_now = True
            result["completed_steps"] = list(completed_steps)
        else:
            result["completed_steps"] = list(completed_steps)
        
        # Find next executable step (respecting dependencies)
        executable = self._get_executable_steps(plan, completed_steps)
        
        if executable:
            # Pick the first executable step (lowest index)
            next_idx = min(executable)
            result["current_step"] = next_idx
            result["next_step"] = plan[next_idx]
        else:
            # No more executable steps - plan is complete
            result["current_step"] = len(plan)
            result["next_step"] = None
        
        # Check if plan is complete
        result["plan_complete"] = len(completed_steps) >= len(plan)
        
        # Update plan_context to reflect progress (shows completed, current, and pending steps)
        result["plan_context"] = self._generate_plan_context(plan, result["current_step"], completed_steps)
        
        return result
    
    def _get_reasoning_service(self):
        """Lazy load ReasoningBookService to avoid circular imports."""
        if self._reasoning_service is None:
            try:
                from modules.reasoning_book.service import service as reasoning_service
                self._reasoning_service = reasoning_service
            except ImportError:
                self._reasoning_service = None
        return self._reasoning_service

    def _extract_user_message(self, input_data: FlowData) -> str:
        """Extract the latest user message from input data."""
        if not input_data:
            return ""
        
        messages = get_messages(input_data)
        
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                    return " ".join(text_parts)
        return ""

    async def receive(self, input_data: dict, config: dict = None) -> dict:
        """
        Creates a step-by-step plan for the user's request.
        
        Input:
            - messages: conversation history
            
        Output:
            - plan: array of steps with {step, action, target, depends_on}
            - current_step: 0
            - original_request: the user's request
            - plan_needed: true/false
            - plan_context: formatted plan string for system prompt
        """
        if input_data is None:
            input_data = {}
        
        # Ensure flow data type safety
        data = ensure_flow_data(input_data)
        
        config = config or {}
        
        if not config.get("enabled", True):
            return input_data
        
        # --- Episode Persistence Support ---
        sm = None
        episode = None
        episode_id = config.get("episode_id")
        enable_episode = config.get("enable_episode_persistence", False)
        
        if enable_episode:
            try:
                sm = get_session_manager()
                
                # Check if we should resume an existing episode
                if episode_id:
                    # Try to load specific episode by ID
                    existing = sm.load_episode_by_id(episode_id)
                    if existing and existing.phase not in [EpisodeState.PHASE_COMPLETED, EpisodeState.PHASE_FAILED]:
                        episode = existing
                        # Restore plan state from episode
                        if existing.plan:
                            data["plan"] = existing.plan
                        if existing.current_step is not None:
                            data["current_step"] = existing.current_step
                        if existing.completed_steps:
                            data["completed_steps"] = existing.completed_steps
                else:
                    # Try to load current episode
                    existing = sm.load_episode_state()
                    if existing and existing.phase not in [EpisodeState.PHASE_COMPLETED, EpisodeState.PHASE_FAILED]:
                        episode = existing
                        # Restore plan state from episode
                        if existing.plan:
                            data["plan"] = existing.plan
                        if existing.current_step is not None:
                            data["current_step"] = existing.current_step
                        if existing.completed_steps:
                            data["completed_steps"] = existing.completed_steps
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)  # Don't fail if episode loading fails
        
        # Check for auto_track mode - if enabled and plan already exists, run tracker progression
        if config.get("auto_track", False):
            existing_plan = data.get("plan", [])
            if existing_plan:
                # Plan exists and auto_track is enabled - run progression tick
                result = self._run_auto_track(data)
                
                # Save episode state if tracking
                if sm and episode:
                    sm.save_episode_state(
                        phase=EpisodeState.PHASE_PLANNING,
                        plan=result.get("plan", []),
                        current_step=result.get("current_step", 0),
                        completed_steps=result.get("completed_steps", []),
                    )
                
                return result
        
        user_request = self._extract_user_message(data)
        
        if not user_request:
            return input_data
        
        # Get planning prompt - check for custom prompt first, then default
        planner_prompt = config.get("planner_prompt")  # Custom user-defined prompt
        if not planner_prompt:
            planner_prompt = config.get(
                "default_planner_prompt",
                "You are a task planner. Analyze the user's request and create a step-by-step execution plan if needed.\n\n"
                "Rules:\n"
                "- If the request is simple (one question, greeting, or single action), return an empty JSON array: []\n"
                "- If the request requires multiple steps, return a JSON array with each step: [{\"action\": \"step description\", \"target\": \"what it applies to\"}]\n"
                "- Keep plans simple and actionable\n"
                "- Maximum {max_steps} steps\n\n"
                "Request: {request}\n\n"
                "Respond with JSON array only."
            )
        
        # Include reasoning context if enabled and available
        if config.get("include_reasoning_context", False):
            reasoning_context = get_reasoning_context(data)
            if reasoning_context:
                planner_prompt += f"\n\nPrevious reasoning context:\n{reasoning_context}\n\nConsider this context when creating your plan."
        
        max_steps = int(config.get("max_steps", 10))
        planner_prompt = planner_prompt.replace("{request}", user_request)
        planner_prompt = planner_prompt.replace("{max_steps}", str(max_steps))
        
        planning_messages = [
            {"role": "system", "content": planner_prompt},
            {"role": "user", "content": user_request}
        ]
        
        plan = []
        
        try:
            # Calculate max_tokens dynamically based on max_steps
            # Base tokens + tokens per step (approx 50 tokens per step for JSON)
            base_tokens = 200
            tokens_per_step = 50
            calculated_max_tokens = base_tokens + (tokens_per_step * max_steps)
            min_tokens = 500
            max_tokens = max(calculated_max_tokens, min_tokens)
            
            response = await self.llm.chat_completion(
                messages=planning_messages,
                model=config.get("model") or settings.get("default_model"),
                temperature=0.1,
                max_tokens=max_tokens
            )
            
            if response and "choices" in response:
                content = response["choices"][0]["message"].get("content", "")
                
                try:
                    # Remove JavaScript-style comments
                    content = re.sub(r'//.*', '', content)
                    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
                    content = content.strip()
                    
                    parsed = json.loads(content)
                    
                    if isinstance(parsed, list):
                        plan = []
                        for i, step in enumerate(parsed[:max_steps]):
                            if isinstance(step, dict):
                                plan.append({
                                    "step": step.get("step", i + 1),
                                    "action": step.get("action", step.get("task", "unknown")),
                                    "target": step.get("target", step.get("query", step.get("details", ""))),
                                    "depends_on": step.get("depends_on")
                                })
                    elif isinstance(parsed, dict):
                        steps = parsed.get("plan", parsed.get("steps", []))
                        if isinstance(steps, list):
                            plan = []
                            for i, step in enumerate(steps[:max_steps]):
                                if isinstance(step, dict):
                                    plan.append({
                                        "step": step.get("step", i + 1),
                                        "action": step.get("action", step.get("task", "unknown")),
                                        "target": step.get("target", step.get("query", step.get("details", ""))),
                                        "depends_on": step.get("depends_on")
                                    })
                except (json.JSONDecodeError, IndexError) as _exc:
                    logger.debug("ignored exception: %s", _exc)
            
            result = input_data.copy()
            result["plan"] = plan
            result["current_step"] = 0
            result["original_request"] = user_request
            result["plan_needed"] = len(plan) > 0
            
            if plan:
                current_step = result["current_step"]
                plan_lines = []
                for i, p in enumerate(plan):
                    step_num = p.get('step', i + 1)
                    action = p['action']
                    target = p.get('target', '')
                    # Mark current step with visual indicator
                    if i == current_step:
                        plan_lines.append(f"→ {step_num}. {action}: {target} (CURRENT)")
                    else:
                        plan_lines.append(f"  {step_num}. {action}: {target}")
                
                plan_text = "\n".join(plan_lines)
                result["plan_context"] = f"## Execution Plan\nCurrently on step {current_step + 1} of {len(plan)}.\n{plan_text}"
            
            # Log plan to Reasoning Book if enabled
            if config.get("log_to_reasoning_book", False):
                reasoning_service = self._get_reasoning_service()
                if reasoning_service:
                    plan_summary = f"Created {len(plan)}-step plan for: {user_request[:100]}..."
                    if plan:
                        plan_summary += f"\nSteps: {', '.join([p['action'] for p in plan[:3]])}"
                        if len(plan) > 3:
                            plan_summary += f" and {len(plan) - 3} more"
                    await reasoning_service.log_thought(plan_summary, source="Planner")
            
            # Save episode state if tracking
            if sm:
                if episode:
                    # Update existing episode
                    sm.save_episode_state(
                        phase=EpisodeState.PHASE_PLANNING if result.get("plan_needed") else EpisodeState.PHASE_COMPLETED,
                        plan=result.get("plan", []),
                        current_step=result.get("current_step", 0),
                        completed_steps=result.get("completed_steps", []),
                    )
                elif enable_episode and result.get("plan_needed"):
                    # Create new episode if plan was created
                    try:
                        budgets = {
                            "max_steps": max_steps,
                        }
                        new_episode = sm.create_episode(
                            input_data={"user_request": user_request},
                            budgets=budgets,
                            metadata={"episode_id": episode_id} if episode_id else {}
                        )
                        sm.save_episode_state(
                            phase=EpisodeState.PHASE_PLANNING,
                            plan=result.get("plan", []),
                            current_step=0,
                            completed_steps=[],
                        )
                        result["episode_id"] = new_episode.episode_id
                    except Exception as _exc:
                        logger.debug("ignored exception: %s", _exc)  # Don't fail if episode saving fails
            
            return result
            
        except Exception as e:
            result = input_data.copy()
            result["plan"] = []
            result["current_step"] = 0
            result["original_request"] = user_request
            result["plan_needed"] = False
            result["planning_error"] = str(e)
            
            # Log error to Reasoning Book if enabled
            if config.get("log_to_reasoning_book", False):
                reasoning_service = self._get_reasoning_service()
                if reasoning_service:
                    await reasoning_service.log_thought(f"Planning failed for: {user_request[:100]}... Error: {str(e)}", source="Planner")
            
            return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data


class PlanStepTracker:
    """
    Tracks progress through a plan with dependency-aware ordering.
    
    Input:
        - plan: array of plan steps (each may have 'depends_on' field)
        - current_step: current step index (default 0)
        
    Output:
        - current_step: next executable step index (respects dependencies)
        - step_completed: the step that was just completed
        - plan_complete: True if all steps are done
        - next_step: the step that should execute next (with dependencies resolved)
        - dependency_error: error message if circular dependencies detected
    """
    
    def _generate_plan_context(self, plan: list, current_step: int, completed_steps: set) -> str:
        """Generate plan_context string showing progress."""
        if not plan:
            return ""
        
        plan_lines = []
        for i, p in enumerate(plan):
            step_num = p.get('step', i + 1)
            action = p['action']
            target = p.get('target', '')
            
            # Mark completed steps, current step, and pending steps
            if i in completed_steps:
                plan_lines.append(f"✓ {step_num}. {action}: {target} (COMPLETED)")
            elif i == current_step:
                plan_lines.append(f"→ {step_num}. {action}: {target} (CURRENT)")
            else:
                plan_lines.append(f"  {step_num}. {action}: {target}")
        
        plan_text = "\n".join(plan_lines)
        return f"## Execution Plan\nCurrently on step {current_step + 1} of {len(plan)}.\n{plan_text}"
    
    def _build_dependency_graph(self, plan: list) -> dict:
        """Build adjacency list and in-degree count for topological sort."""
        n = len(plan)
        adj = {i: [] for i in range(n)}  # step_index -> [dependent_step_indices]
        in_degree = {i: 0 for i in range(n)}  # step_index -> number of dependencies
        
        # Map step numbers to indices for dependency resolution
        step_num_to_idx = {}
        for i, step in enumerate(plan):
            step_num = step.get('step', i + 1)
            step_num_to_idx[step_num] = i
        
        for i, step in enumerate(plan):
            depends_on = step.get('depends_on')
            if depends_on is not None:
                # Handle single dependency or list of dependencies
                if isinstance(depends_on, int):
                    deps = [depends_on]
                elif isinstance(depends_on, list):
                    deps = depends_on
                else:
                    deps = []
                
                for dep_step_num in deps:
                    # Find the index of the dependency step
                    dep_idx = step_num_to_idx.get(dep_step_num)
                    if dep_idx is not None and dep_idx < len(plan):
                        # Add edge: dep_idx -> i (i depends on dep_idx)
                        adj[dep_idx].append(i)
                        in_degree[i] += 1
        
        return adj, in_degree
    
    def _detect_circular_dependencies(self, plan: list) -> tuple:
        """Detect circular dependencies using Kahn's algorithm."""
        if not plan:
            return False, None
        
        adj, in_degree = self._build_dependency_graph(plan)
        n = len(plan)
        
        # Find all steps with no dependencies
        queue = deque([i for i in range(n) if in_degree[i] == 0])
        visited_count = 0
        visited_order = []
        
        while queue:
            u = queue.popleft()
            visited_order.append(u)
            visited_count += 1
            
            for v in adj[u]:
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)
        
        if visited_count != n:
            # Circular dependency detected
            unvisited = [i for i in range(n) if i not in visited_order]
            return True, unvisited
        
        return False, None
    
    def _get_executable_steps(self, plan: list, completed_steps: set) -> list:
        """Get list of step indices that can be executed (all dependencies satisfied)."""
        if not plan:
            return []
        
        adj, in_degree = self._build_dependency_graph(plan)
        n = len(plan)
        # Build once — reused for every step's dependency check (O(n) not O(n²)).
        step_num_to_idx = {plan[j].get('step', j + 1): j for j in range(n)}

        # Calculate which steps have all dependencies completed
        executable = []
        for i in range(n):
            if i in completed_steps:
                continue  # Already done

            # Check if all dependencies are completed
            deps_satisfied = True
            step = plan[i]
            depends_on = step.get('depends_on')

            if depends_on is not None:
                if isinstance(depends_on, int):
                    deps = [depends_on]
                elif isinstance(depends_on, list):
                    deps = depends_on
                else:
                    deps = []

                for dep_step_num in deps:
                    dep_idx = step_num_to_idx.get(dep_step_num)
                    if dep_idx is not None and dep_idx not in completed_steps:
                        deps_satisfied = False
                        break

            if deps_satisfied:
                executable.append(i)

        return executable
    
    async def receive(self, data: dict, config: dict = None) -> dict:
        if not isinstance(data, dict):
            return data
            
        plan = data.get("plan", [])
        current = data.get("current_step", 0)
        completed_steps = set(data.get("completed_steps", []))
        
        # Only track if there's a plan
        if not plan:
            return data
        
        result = data.copy()
        
        # Check for circular dependencies
        has_cycle, cycle_steps = self._detect_circular_dependencies(plan)
        if has_cycle:
            result["dependency_error"] = f"Circular dependency detected in steps: {[plan[i].get('step', i+1) for i in cycle_steps]}"
            result["plan_complete"] = True  # Stop execution
            return result
        
        # Resume mode: if 'completed_steps' provided, assume current already completed
        if "completed_steps" in data and current < len(plan):
            # Don't auto-complete current if resume from completed state
            if current not in completed_steps:
                completed_steps.add(current)
                result["step_completed"] = plan[current]
            result["completed_steps"] = list(completed_steps)
        else:
            # Normal mode: always complete current
            if 0 <= current < len(plan):
                completed_steps.add(current)
                result["step_completed"] = plan[current]
                result["completed_steps"] = list(completed_steps)
        
        # Find next executable step (respecting dependencies)
        executable = self._get_executable_steps(plan, completed_steps)
        
        if executable:
            # Pick the first executable step (lowest index)
            next_idx = min(executable)
            result["current_step"] = next_idx
            result["next_step"] = plan[next_idx]
        else:
            # No more executable steps - plan is complete
            result["current_step"] = len(plan)
            result["next_step"] = None
        
        # Check if plan is complete
        result["plan_complete"] = len(completed_steps) >= len(plan)
        
        # Update plan_context to reflect progress (shows completed, current, and pending steps)
        result["plan_context"] = self._generate_plan_context(plan, result["current_step"], completed_steps)
        
        return result

    async def send(self, processed_data: dict) -> dict:
        return processed_data


async def get_executor_class(node_type_id: str):
    if node_type_id == "planner":
        return PlannerExecutor
    if node_type_id == "plan_step_tracker":
        return PlanStepTracker
    return None
