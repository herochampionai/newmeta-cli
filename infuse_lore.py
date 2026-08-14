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
target_files = ['cli.py', 'explorer.py', 'newmeta_tui.py', 'archon_tui.py', 'archon_run.py', 'PIKA_POKE.md']

for root, _, files in os.walk(repo_path):
    for file in files:
        if file in target_files:
            file_path = os.path.join(root, file)
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original = content
            # Only replace inside strings to avoid breaking logic (basic approach for quotes)
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
                print(f'Infused lore into {file_path}')
