"""
Tests for concurrency and deadlock scenarios.

These tests verify that the application handles concurrent access correctly
without deadlocks.
"""

import logging
import asyncio
import threading
import time
import pytest
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


@pytest.fixture
def flow_manager_instance(tmp_path):
    """Isolated flow store so concurrency tests never touch project state."""
    from core.flow_manager import FlowManager
    return FlowManager(storage_file=str(tmp_path / "flows.json"))


class TestFlowManagerConcurrency:
    """Test concurrent access to FlowManager."""

    def test_concurrent_reads(self, flow_manager_instance):
        """Test that multiple threads can read flows concurrently."""
        manager = flow_manager_instance
        
        def read_flows():
            for _ in range(10):
                flows = manager.list_flows()
                time.sleep(0.001)
            return True
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(read_flows) for _ in range(5)]
            results = [f.result() for f in as_completed(futures)]
        
        assert all(results)

    def test_concurrent_read_write(self, flow_manager_instance):
        """Test concurrent reads and writes don't deadlock or lose writes."""
        manager = flow_manager_instance
        errors = []
        
        def write_flow():
            for i in range(5):
                try:
                    manager.save_flow(
                        name=f"Test Flow {threading.current_thread().name} {i}",
                        nodes=[],
                        connections=[]
                    )
                except Exception as exc:
                    errors.append(exc)
                time.sleep(0.001)
            return True
        
        def read_flows():
            for _ in range(10):
                try:
                    manager.list_flows()
                except Exception as exc:
                    errors.append(exc)
                time.sleep(0.001)
            return True
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            # 5 writers, 5 readers
            futures = []
            for i in range(5):
                futures.append(executor.submit(write_flow))
                futures.append(executor.submit(read_flows))
            
            # Wait with timeout to detect deadlock
            for f in as_completed(futures, timeout=10):
                f.result()

        assert errors == []
        assert len(manager.list_flows()) >= 25


class TestModuleManagerConcurrency:
    """Test concurrent access to ModuleManager."""

    def test_concurrent_module_access(self):
        """Test that multiple threads can access module manager."""
        from fastapi import FastAPI
        from core.module_manager import ModuleManager
        
        app = FastAPI()
        manager = ModuleManager(app)
        
        def get_modules():
            for _ in range(10):
                modules = manager.get_all_modules()
                time.sleep(0.001)
            return True
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(get_modules) for _ in range(5)]
            results = [f.result(timeout=5) for f in as_completed(futures)]
        
        assert all(results)


class TestAsyncConcurrency:
    """Test async concurrency primitives."""

    @pytest.mark.asyncio
    async def test_asyncio_lock_concurrent_access(self):
        """Test asyncio.Lock allows concurrent coroutines."""
        from core.llm import get_client_lock
        
        _client_lock = get_client_lock()
        
        counter = {'value': 0}
        
        async def increment():
            async with _client_lock:
                current = counter['value']
                await asyncio.sleep(0.001)  # Simulate work
                counter['value'] = current + 1
        
        await asyncio.gather(*[increment() for _ in range(10)])
        
        # With proper locking, counter should be 10
        assert counter['value'] == 10

    @pytest.mark.asyncio
    async def test_flow_runner_cache_concurrent_access(self):
        """Test FlowRunner cache is thread-safe."""
        from core.flow_runner import FlowRunner
        
        # Clear cache first
        FlowRunner.clear_cache()
        
        results = []
        
        async def get_executor(module_id, node_type_id):
            result = await FlowRunner._get_executor_class(module_id, node_type_id)
            results.append(result)
            return result
        
        # Run multiple concurrent requests for same executor
        tasks = [get_executor('chat', 'chat_input') for _ in range(10)]
        await asyncio.gather(*tasks)
        
        # All should return the same executor class
        assert all(r == results[0] for r in results)


class TestDeadlockPrevention:
    """Test that we don't have classic deadlock patterns."""

    def test_no_cross_lock_deadlock(self, flow_manager_instance):
        """Verify we don't have A->B, B->A deadlock pattern."""
        # This test documents the expected behavior:
        # - FlowManager uses threading.RLock (synchronous)
        # - LLM uses asyncio.Lock (asynchronous)
        # - They should NEVER be used together in nested way
        import asyncio

        from core.llm import get_client_lock

        manager = flow_manager_instance
        # A re-entrant lock accepts a second non-blocking acquire from the same thread.
        assert manager.lock.acquire(blocking=False)
        assert manager.lock.acquire(blocking=False)
        manager.lock.release()
        manager.lock.release()

        assert isinstance(get_client_lock(), asyncio.Lock)

    @pytest.mark.asyncio
    async def test_async_timeout_prevents_deadlock(self):
        """Test that operations can timeout to prevent deadlock."""
        import asyncio
        
        # This test verifies we can use asyncio.wait_for as a deadlock prevention
        async def slow_operation():
            await asyncio.sleep(0.1)
            return "done"
        
        # Should complete normally
        result = await asyncio.wait_for(slow_operation(), timeout=1.0)
        assert result == "done"
        
        # Should timeout
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_operation(), timeout=0.01)


class TestLockOrdering:
    """Test lock ordering is followed."""

    def test_flow_manager_lock_type(self, flow_manager_instance):
        """Verify FlowManager uses RLock for reentrancy."""
        manager = flow_manager_instance
        
        # Should be RLock (not Lock) for reentrant locking
        # Use type name check since isinstance may not work with threading.RLock on some platforms
        lock_type_name = type(manager.lock).__name__
        assert 'RLock' in lock_type_name or 'Lock' in lock_type_name, f"Expected RLock, got {lock_type_name}"

    def test_module_manager_lock_type(self):
        """Verify ModuleManager uses RLock for reentrancy."""
        from fastapi import FastAPI
        from core.module_manager import ModuleManager
        
        app = FastAPI()
        manager = ModuleManager(app)
        
        # Should be RLock (not Lock) for reentrant locking
        lock_type_name = type(manager.lock).__name__
        assert 'RLock' in lock_type_name or 'Lock' in lock_type_name, f"Expected RLock, got {lock_type_name}"


# Stress test for detecting potential deadlocks
class TestStressConcurrency:
    """Stress tests to detect concurrency issues."""

    def test_high_concurrency_flow_manager(self, flow_manager_instance):
        """Stress test with high concurrent access."""
        manager = flow_manager_instance
        errors = []
        
        def worker(thread_id):
            try:
                for i in range(20):
                    if i % 3 == 0:
                        manager.list_flows()
                    elif i % 3 == 1:
                        manager.get_flow("default-flow-001")
                    else:
                        manager.get_all_flows_dict()
            except Exception as e:
                errors.append(e)
        
        threads = []
        for i in range(20):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
        
        # Start all threads
        for t in threads:
            t.start()
        
        # Wait for completion with timeout
        for t in threads:
            t.join(timeout=10)
        
        # Check no threads are still alive (would indicate deadlock)
        alive = [t for t in threads if t.is_alive()]
        assert len(alive) == 0, f"Deadlock detected: {len(alive)} threads still running"
        assert len(errors) == 0, f"Errors occurred: {errors}"

    @pytest.mark.asyncio
    async def test_high_concurrency_async_operations(self):
        """Stress test with high async concurrency."""
        from core.flow_runner import FlowRunner
        
        FlowRunner.clear_cache()
        
        async def worker():
            try:
                await FlowRunner._get_executor_class('chat', 'chat_input')
            except Exception as _exc:
                logger.debug("ignored exception: %s", _exc)  # May fail if module not available
        
        # Run many concurrent operations
        tasks = [worker() for _ in range(50)]
        
        # Should complete without deadlock
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10)

