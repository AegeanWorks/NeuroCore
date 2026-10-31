"""
Shared utility class for plan dependency management.

This module consolidates the duplicated dependency logic that was previously
scattered across PlannerExecutor and PlanStepTracker classes.
"""

from collections import deque
from typing import List, Dict, Set, Tuple, Optional


class PlanHelper:
    """
    Shared utility class for plan dependency management.
    
    Provides methods for:
    - Building dependency graphs from plan steps
    - Detecting circular dependencies
    - Finding executable steps based on dependencies
    - Generating plan context strings
    """
    
    @staticmethod
    def build_dependency_graph(plan: List[Dict]) -> Tuple[Dict, Dict]:
        """
        Build adjacency list and in-degree count for topological sort.
        
        Args:
            plan: List of plan step dictionaries, each may have 'depends_on' field.
            
        Returns:
            Tuple of (adjacency_dict, in_degree_dict):
            - adj: Dict mapping step index -> list of dependent step indices
            - in_degree: Dict mapping step index -> number of dependencies
        """
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
    
    @staticmethod
    def detect_circular_dependencies(plan: List[Dict]) -> Tuple[bool, Optional[List[int]]]:
        """
        Detect circular dependencies using Kahn's algorithm.
        
        Args:
            plan: List of plan step dictionaries.
            
        Returns:
            Tuple of (has_cycle, cycle_step_indices):
            - has_cycle: True if circular dependencies detected
            - cycle_step_indices: List of step indices involved in the cycle, or None
        """
        if not plan:
            return False, None
        
        adj, in_degree = PlanHelper.build_dependency_graph(plan)
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
    
    @staticmethod
    def get_executable_steps(plan: List[Dict], completed_steps: Set[int]) -> List[int]:
        """
        Get list of step indices that can be executed (all dependencies satisfied).
        
        Args:
            plan: List of plan step dictionaries.
            completed_steps: Set of step indices that have been completed.
            
        Returns:
            List of step indices that can be executed next.
        """
        if not plan:
            return []
        
        n = len(plan)
        
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
                
                # Map step numbers to indices
                step_num_to_idx = {plan[j].get('step', j + 1): j for j in range(n)}
                
                for dep_step_num in deps:
                    dep_idx = step_num_to_idx.get(dep_step_num)
                    if dep_idx is not None and dep_idx not in completed_steps:
                        deps_satisfied = False
                        break
            
            if deps_satisfied:
                executable.append(i)
        
        return executable
    
    @staticmethod
    def generate_plan_context(plan: List[Dict], current_step: int, completed_steps: Set[int]) -> str:
        """
        Generate plan_context string showing progress.
        
        Args:
            plan: List of plan step dictionaries.
            current_step: Current step index.
            completed_steps: Set of completed step indices.
            
        Returns:
            Formatted string showing plan progress.
        """
        if not plan:
            return ""
        
        plan_lines = []
        for i, p in enumerate(plan):
            step_num = p.get('step', i + 1)
            action = p.get('action', 'Unknown')
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
    
    @staticmethod
    def validate_dependencies(plan: List[Dict]) -> Tuple[bool, Optional[str]]:
        """
        Validate that all dependencies in the plan are valid.
        
        Args:
            plan: List of plan step dictionaries.
            
        Returns:
            Tuple of (is_valid, error_message):
            - is_valid: True if all dependencies are valid
            - error_message: Error message if validation failed, None otherwise
        """
        if not plan:
            return True, None
        
        n = len(plan)
        step_nums = set()
        
        # Collect all valid step numbers
        for i, step in enumerate(plan):
            step_num = step.get('step', i + 1)
            step_nums.add(step_num)
        
        # Check each dependency
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
                    if dep_step_num not in step_nums:
                        return False, f"Step {step.get('step', i + 1)} depends on non-existent step {dep_step_num}"
                    # Check for self-dependency
                    step_num = step.get('step', i + 1)
                    if dep_step_num == step_num:
                        return False, f"Step {step_num} cannot depend on itself"
        
        return True, None

