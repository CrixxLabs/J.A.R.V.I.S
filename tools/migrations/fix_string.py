with open(r'D:\J.A.R.V.I.S\executor.py', 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

# Fix the broken string at lines 1695-1697 (0-indexed: 1694-1696)
# Replace lines 1695-1697 with a single correct line
lines[1694] = '        return _stable_success("Vision subsystem status:\\n" + "\\n".join(lines))\n'
# Remove the broken lines 1695-1696 (now 1695-1696 after replacement)
del lines[1695:1697]

with open(r'D:\J.A.R.V.I.S\executor.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Fixed')