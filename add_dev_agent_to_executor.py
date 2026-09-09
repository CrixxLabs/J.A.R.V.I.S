with open(r'D:\J.A.R.V.I.S\executor.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Add dev_agent import after memory import
import_marker = 'from memory import remember, recall_all, log_activity, update_daily_stats, log_failure'
idx = content.find(import_marker)
if idx >= 0:
    idx_end = content.find('\n', idx) + 1
    dev_import = '\n# Dev Agent (MARK VII)\ntry:\n    from dev_agent import DEV_AGENT_ACTIONS\n    _DEV_AGENT_AVAILABLE = True\nexcept Exception as _dev_err:\n    _DEV_AGENT_AVAILABLE = False\n    print(f"[executor] Dev Agent not available: {_dev_err}")\n'
    content = content[:idx_end] + dev_import + content[idx_end:]

# Find where to add action handlers in execute() - after self_changes
handler_marker = 'if act == "self_changes":'
idx = content.find(handler_marker)
if idx >= 0:
    # Find the end of this block
    idx2 = content.find('return _stable_failure("Unsupported action.")', idx)
    if idx2 >= 0:
        dev_handlers = '''
        # Dev Agent actions (MARK VII)
        if _DEV_AGENT_AVAILABLE:
            if act == "dev_inspect":
                return DEV_AGENT_ACTIONS["dev_inspect"](action)
            if act == "dev_test":
                return DEV_AGENT_ACTIONS["dev_test"](action)
            if act == "dev_search":
                return DEV_AGENT_ACTIONS["dev_search"](action)
            if act == "dev_propose":
                return DEV_AGENT_ACTIONS["dev_propose"](action)
            if act == "dev_status":
                return DEV_AGENT_ACTIONS["dev_status"](action)

'''
        content = content[:idx2] + dev_handlers + content[idx2:]

# Find AVAILABLE_ACTIONS_LIST and add dev agent actions
list_marker = '# Vision/Eyes (MARK VII)'
idx = content.find(list_marker)
if idx >= 0:
    idx2 = content.find(']', idx)
    if idx2 >= 0:
        dev_actions = '''
    # Dev Agent (MARK VII)
    "dev_inspect",           # inspect file structure and content
    "dev_test",              # run tests and analyze failures
    "dev_search",            # search code for patterns
    "dev_propose",           # propose code changes (diff)
    "dev_status",            # get dev agent status
'''
        content = content[:idx2] + dev_actions + content[idx2:]

# Find _NO_RETRY_ACTIONS and add dev agent actions
noretry_marker = '"capture_webcam", "capture_screen_region",'
idx = content.find(noretry_marker)
if idx >= 0:
    idx2 = content.find('}', idx)
    if idx2 >= 0:
        dev_noretry = '''
        "dev_inspect", "dev_test", "dev_search", "dev_propose", "dev_status",
'''
        content = content[:idx2] + dev_noretry + content[idx2:]

with open(r'D:\J.A.R.V.I.S\executor.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Dev agent integration added')