def test_package_exposes_version():
    import chat_agent_bridge
    assert chat_agent_bridge.__version__ == '0.2.3'
