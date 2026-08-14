import os
import re

lore_map = {
    r'\b[Hh]acker\b': 'Invoker',
    r'\b[Hh]acking\b': 'Casting Spells',
    r'\b[Hh]acks?\b': 'Spell(s)',
    r'\b[Aa]gents?\b': 'Companion(s)',
    r'\b[Mm]odels?\b': 'Pokemon',
    r'\b[Ss]uccess(fully)?\b': 'Flawless Victory',
    r'\b[Ee]rror\b': 'Raid Wipe',
    r'\b[Ff]ailed\b': 'Fed First Blood',
    r'\b[Pp]rocess\b': 'Raid Boss',
    r'\b[Tt]ask\b': 'Quest'
}

repo_path = r'D:\Codes\pika poke\NewMeta'
exclude_dirs = {'venv', '.venv', '.git', '__pycache__', 'node_modules'}

for root, dirs, files in os.walk(repo_path):
    # Exclude bad directories
    dirs[:] = [d for d in dirs if d not in exclude_dirs]
    
    for file in files:
        if file.endswith('.py') or file.endswith('.md'):
            # Skip the script itself
            if file == 'infuse_lore_global.py':
                continue
                
            file_path = os.path.join(root, file)
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original = content
            
            def replacer(match):
                text = match.group(0)
                for pattern, replacement in lore_map.items():
                    text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
                return text

            # Replace inside double and single quotes
            content = re.sub(r'\"(.*?)\"', replacer, content)
            content = re.sub(r'\'(.*?)\'', replacer, content)

            if original != content:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f'Infused global lore into {file_path}')
