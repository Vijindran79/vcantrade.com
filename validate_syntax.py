import py_compile
import sys

files = ['config.py', 'core/brain_swarm.py', 'core/symbol_mapper.py']
results = []

for f in files:
    try:
        py_compile.compile(f, doraise=True)
        results.append(f"{f}: OK")
    except py_compile.PyCompileError as e:
        results.append(f"{f}: ERROR - {e}")

with open('syntax_results.txt', 'w') as out:
    for r in results:
        out.write(r + '\n')