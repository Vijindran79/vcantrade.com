import ast
import sys

try:
    with open('main.py', 'r', encoding='utf-8') as f:
        source = f.read()
    ast.parse(source)
    print('main.py: syntax OK')
    print(f'Lines: {len(source.splitlines())}')
    sys.exit(0)
except SyntaxError as e:
    print(f'main.py: SYNTAX ERROR at line {e.lineno}: {e.msg}')
    sys.exit(1)
except Exception as e:
    print(f'main.py: ERROR: {e}')
    sys.exit(1)
