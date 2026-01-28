import csv
import os
import re

def apply_html_translations():
    with open('i18n/extraction_report.csv', mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)
    
    # Filter for HTML/JS files
    front_files = [row for row in all_rows if row['file_path'].endswith(('.html', '.js'))]
    
    # Group by file
    file_groups = {}
    for row in front_files:
        path = row['file_path']
        if path not in file_groups:
            file_groups[path] = []
        file_groups[path].append(row)
    
    for file_path, rows in file_groups.items():
        if not os.path.exists(file_path):
            continue
            
        print(f"Processing {file_path}...")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Sort by phrase length descending to avoid partial replacement issues
        rows.sort(key=lambda x: len(x['original_line']), reverse=True)

        for row in rows:
            orig = row['original_line']
            trans = row['translated_line']
            
            # Check if trans actually has $id_N (it should)
            if "$id_" in trans:
                if orig in content:
                    content = content.replace(orig, trans)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

if __name__ == "__main__":
    apply_html_translations()
