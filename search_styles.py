import os
import re

search_dir = r"D:\project1\Sarna_Broker_Website-2"
pattern = re.compile(r'\.brand-badge\s*\{([^}]+)\}')

for root, dirs, files in os.walk(search_dir):
    for file in files:
        if file.endswith(('.html', '.css', '.js')):
            path = os.path.join(root, file)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    matches = pattern.findall(content)
                    if matches:
                        print(f"Found in {path}:")
                        for match in matches:
                            print(match.strip())
                            print("-" * 20)
            except Exception as e:
                pass
