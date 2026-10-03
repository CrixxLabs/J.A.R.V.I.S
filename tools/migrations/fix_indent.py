with open(r'D:\J.A.R.V.I.S\executor.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

# Fix the indentation of the "Unsupported action" line and ensure proper structure
# Line 2029 (0-indexed: 2028) should be indented
lines[2028] = '        return _stable_failure("Unsupported action.")\n'

with open(r'D:\J.A.R.V.I.S\executor.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed indentation')