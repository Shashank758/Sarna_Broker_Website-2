import re

path = r"D:\project1\Sarna_Broker_Website-2\app.py"
pattern = re.compile(r'def\s+(\w+)\s*\(')

with open(path, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        if 'run_migrations' in line:
            print(f"Line {i}: {line.strip()}")
        match = pattern.search(line)
        if match:
            func_name = match.group(1)
            if 'migration' in func_name or 'upgrade' in func_name:
                print(f"Line {i}: def {func_name}")
