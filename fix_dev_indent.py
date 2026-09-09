with open(r'D:\J.A.R.V.I.S\executor.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

# Fix line 2065 (0-indexed: 2064)
lines[2064] = '        return _stable_failure("Unsupported action.")\n'

with open(r'D:\J.A.R.V.I.S\executor.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed')