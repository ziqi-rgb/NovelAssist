import sys
sys.stdout.reconfigure(encoding='utf-8')

with open('NovelAssist/frontend/app.js', 'r', encoding='utf-8') as f:
    lines = f.readlines()

line = lines[5108]  # 0-indexed = line 5109
print(f'Line 5109 length: {len(line)}')

# Simple count ignoring everything
parens = 0
braces = 0
for ch in line:
    if ch == '(':
        parens += 1
    elif ch == ')':
        parens -= 1
    elif ch == '{':
        braces += 1
    elif ch == '}':
        braces -= 1

print(f'Raw count: parens={parens}, braces={braces}')

# Check the line before (5108) and after (5110)
for idx in [5107, 5108, 5109]:
    l = lines[idx]
    p = b = 0
    for ch in l:
        if ch == '(': p += 1
        elif ch == ')': p -= 1
        elif ch == '{': b += 1
        elif ch == '}': b -= 1
    print(f'Line {idx+1}: parens={p}, braces={b}: {l.strip()[:100]}')
