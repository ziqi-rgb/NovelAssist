import sys
sys.stdout.reconfigure(encoding='utf-8')

with open('NovelAssist/frontend/app.js', 'r', encoding='utf-8') as f:
    lines = f.readlines()

cum_p = 0
cum_b = 0
last_zero = 0
for i, line in enumerate(lines, 1):
    for ch in line:
        if ch == '(': cum_p += 1
        elif ch == ')': cum_p -= 1
        elif ch == '{': cum_b += 1
        elif ch == '}': cum_b -= 1
    if cum_p == 0 and cum_b == 0:
        last_zero = i

print(f'Last balanced line (raw): {last_zero}')
print(f'Final cum_p={cum_p}, cum_b={cum_b}')

# Show lines from last_zero to see where it goes wrong
cum_p = 0
cum_b = 0
for i, line in enumerate(lines, 1):
    prev_p, prev_b = cum_p, cum_b
    for ch in line:
        if ch == '(': cum_p += 1
        elif ch == ')': cum_p -= 1
        elif ch == '{': cum_b += 1
        elif ch == '}': cum_b -= 1
    if (prev_p == 0 and prev_b == 0) and (cum_p != 0 or cum_b != 0):
        print(f'First unbalanced at line {i}: cum_p={cum_p}, cum_b={cum_b}')
        print(f'  {lines[i-1].rstrip()[:200]}')
    if cum_p < 0:
        print(f'NEGATIVE paren at line {i}: cum_p={cum_p}')
        break
    if cum_b < 0:
        print(f'NEGATIVE brace at line {i}: cum_b={cum_b}')
        break
