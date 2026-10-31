import pytest
from unittest.mock import MagicMock, patch
from core.flow_runner import FlowRunner


@pytest.fixture
def mock_module_manager():
    """Create a mock module manager."""
    manager = MagicMock()
    manager.get_all_modules.return_value = [
        {'id': 'chat', 'enabled': True},
        {'id': 'llm_module', 'enabled': True},
        {'id': 'memory', 'enabled': True},
        {'id': 'tools', 'enabled': True},
        {'id': 'system_prompt', 'enabled': False},  # Disabled module
    ]
    return manager


@pytest.fixture
def basic_flow():
    """Create a basic valid flow."""
    return {
        'id': 'test-flow',
        'name': 'Test Flow',
        'nodes': [
            {
                'id': 'node-1',
                'name': 'Chat Input',
                'moduleId': 'chat',
                'nodeTypeId': 'chat_input',
                'x': 100,
                'y': 100
            },
            {
                'id': 'node-2',
                'name': 'LLM Core',
                'moduleId': 'llm_module',
                'nodeTypeId': 'llm_core',
                'x': 300,
                'y': 100
            },
            {
                'id': 'node-3',
                'name': 'Chat Output',
                'moduleId': 'chat',
                'nodeTypeId': 'chat_output',
                'x': 500,
                'y': 100
            }
        ],
        'connections': [
            {'from': 'node-1', 'to': 'node-2'},
            {'from': 'node-2', 'to': 'node-3'}
        ],
        'bridges': []
    }


@pytest.fixture
def flow_with_disabled_module():
    """Create a flow with a node referencing disabled module."""
    return {
        'id': 'test-flow',
        'name': 'Test Flow',
        'nodes': [
            {
                'id': 'node-1',
                'name': 'System Prompt',
                'moduleId': 'system_prompt',  # This module is disabled
                'nodeTypeId': 'system_prompt',
                'x': 100,
                'y': 100
            }
        ],
        'connections': [],
        'bridges': []
    }


@pytest.fixture
def flow_with_orphaned_connection():
    """Create a flow with orphaned connections."""
    return {
        'id': 'test-flow',
        'name': 'Test Flow',
        'nodes': [
            {
                'id': 'node-1',
                'name': 'Chat Input',
                'moduleId': 'chat',
                'nodeTypeId': 'chat_input',
                'x': 100,
                'y': 100
            }
        ],
        'connections': [
            {'from': 'node-1', 'to': 'node-999'}  # Orphaned
        ],
        'bridges': []
    }


@pytest.fixture
def flow_with_unconnected_node():
    """Create a flow with unconnected (non-trigger) nodes."""
    return {
        'id': 'test-flow',
        'name': 'Test Flow',
        'nodes': [
            {
                'id': 'node-1',
                'name': 'LLM Core',
                'moduleId': 'llm_module',
                'nodeTypeId': 'llm_core',
                'x': 100,
                'y': 100
            }
        ],
        'connections': [],
        'bridges': []
    }


@pytest.fixture
def flow_with_bridges():
    """Create a flow with bridged nodes."""
    return {
        'id': 'test-flow',
        'name': 'Test Flow',
        'nodes': [
            {
                'id': 'node-1',
                'name': 'Chat Input',
                'moduleId': 'chat',
                'nodeTypeId': 'chat_input',
                'x': 100,
                'y': 100
            },
            {
                'id': 'node-2',
                'name': 'LLM Core',
                'moduleId': 'llm_module',
                'nodeTypeId': 'llm_core',
                'x': 300,
                'y': 100
            }
        ],
        'connections': [
            {'from': 'node-1', 'to': 'node-2'}
        ],
        'bridges': [
            {'from': 'node-1', 'to': 'node-2'}
        ]
    }


@pytest.fixture
def flow_with_annotation():
    """Create a flow with annotation/comment nodes."""
    return {
        'id': 'test-flow',
        'name': 'Test Flow',
        'nodes': [
            {
                'id': 'node-1',
                'name': 'Chat Input',
                'moduleId': 'chat',
                'nodeTypeId': 'chat_input',
                'x': 100,
                'y': 100
            },
            {
                'id': 'node-2',
                'name': 'My Annotation',
                'moduleId': 'annotations',
                'nodeTypeId': 'annotation',
                'x': 300,
                'y': 100
            }
        ],
        'connections': [
            {'from': 'node-1', 'to': 'node-2'}
        ],
        'bridges': []
    }


def test_validate_valid_flow(basic_flow, mock_module_manager):
    """Test validation of a valid flow."""
    runner = FlowRunner('test-flow', flow_override=basic_flow)
    result = runner.validate(mock_module_manager)
    
    assert result['valid'] is True
    assert len(result['issues']) == 0
    assert len(result['warnings']) == 0


def test_validate_disabled_module(flow_with_disabled_module, mock_module_manager):
    """Test validation detects nodes referencing disabled modules."""
    runner = FlowRunner('test-flow', flow_override=flow_with_disabled_module)
    result = runner.validate(mock_module_manager)
    
    assert result['valid'] is False
    assert len(result['issues']) > 0
    assert any(i['type'] == 'disabled_module' for i in result['issues'])


def test_validate_orphaned_connection(flow_with_orphaned_connection, mock_module_manager):
    """Test validation detects orphaned connections."""
    runner = FlowRunner('test-flow', flow_override=flow_with_orphaned_connection)
    result = runner.validate(mock_module_manager)
    
    assert len(result['issues']) > 0
    assert any(i['type'] == 'orphaned_connection' for i in result['issues'])


def test_validate_unconnected_node(flow_with_unconnected_node, mock_module_manager):
    """Test validation warns about unconnected nodes."""
    runner = FlowRunner('test-flow', flow_override=flow_with_unconnected_node)
    result = runner.validate(mock_module_manager)
    
    assert len(result['warnings']) > 0
    assert any(w['type'] == 'unconnected_node' for w in result['warnings'])


def test_validate_bridged_nodes_count_as_connected(flow_with_bridges, mock_module_manager):
    """Test that bridged nodes are considered connected."""
    runner = FlowRunner('test-flow', flow_override=flow_with_bridges)
    result = runner.validate(mock_module_manager)
    
    # Should not warn about unconnected nodes since bridges count
    assert not any(w['type'] == 'unconnected_node' for w in result['warnings'])


def test_validate_annotation_no_warning(flow_with_annotation, mock_module_manager):
    """Test that annotation/comment nodes don't trigger unconnected warnings."""
    runner = FlowRunner('test-flow', flow_override=flow_with_annotation)
    result = runner.validate(mock_module_manager)
    
    # Should not warn about unconnected annotation
    assert not any(w['type'] == 'unconnected_node' for w in result['warnings'])


def test_validate_trigger_node_no_warning(mock_module_manager):
    """Test that trigger nodes don't trigger unconnected warnings."""
    flow = {
        'id': 'test-flow',
        'name': 'Test Flow',
        'nodes': [
            {
                'id': 'node-1',
                'name': 'Trigger',
                'moduleId': 'logic',
                'nodeTypeId': 'trigger_node',
                'x': 100,
                'y': 100
            }
        ],
        'connections': [],
        'bridges': []
    }
    runner = FlowRunner('test-flow', flow_override=flow)
    result = runner.validate(mock_module_manager)
    
    assert not any(w['type'] == 'unconnected_node' for w in result['warnings'])


def test_validate_repeater_node_no_warning(mock_module_manager):
    """Test that repeater nodes don't trigger unconnected warnings."""
    flow = {
        'id': 'test-flow',
        'name': 'Test Flow',
        'nodes': [
            {
                'id': 'node-1',
                'name': 'Repeater',
                'moduleId': 'logic',
                'nodeTypeId': 'repeater_node',
                'x': 100,
                'y': 100
            }
        ],
        'connections': [],
        'bridges': []
    }
    runner = FlowRunner('test-flow', flow_override=flow)
    result = runner.validate(mock_module_manager)
    
    assert not any(w['type'] == 'unconnected_node' for w in result['warnings'])
