with open(r'D:\J.A.R.V.I.S\executor.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Find where to add action handlers in execute() - look for 'self_changes' handler
idx = content.find('if act == "self_changes":')
if idx >= 0:
    # Find the end of that block - the "Unsupported action" return
    idx2 = content.find('return _stable_failure("Unsupported action.")', idx)
    print(f'Found self_changes at {idx}, unsupported at {idx2}')
    print(content[idx:idx2+100])
else:
    print('self_changes not found')
    # Search for the pattern
    idx = content.find('self_changes')
    print(f'self_changes found at: {idx}')
    if idx >= 0:
        print(content[idx:idx+200])