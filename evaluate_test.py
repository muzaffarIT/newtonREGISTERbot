import re

def cyrillic_to_latin(text: str) -> str:
    mapping = {
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'YO', 'Ж': 'ZH', 'З': 'Z',
        'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M', 'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R',
        'С': 'S', 'Т': 'T', 'У': 'U', 'Ф': 'F', 'Х': 'X', 'Ц': 'TS', 'Ч': 'CH', 'Ш': 'SH', 'Щ': 'SHCH',
        'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'YU', 'Я': 'YA',
    }
    for cyr, lat in mapping.items():
        text = text.replace(cyr, lat)
    return text

def _normalize(s: str) -> str:
    return str(s).strip().upper()

def _evaluate_class_match(row_group: str, row_class: str, grade: str) -> int:
    g = _normalize(grade)
    rg = _normalize(row_group)
    rc = _normalize(row_class)
    
    if "ПОЧЕМУЧК" in g:
        return 2 if "ПОЧЕМУЧК" in rg or "ПОЧЕМУЧК" in rc else 0
    
    if rc == g:
        return 2 # Exact match
        
    g_digits = re.findall(r'\d+', g)
    rc_digits = re.findall(r'\d+', rc)
    if not (g_digits and rc_digits and g_digits[0] == rc_digits[0]):
        return 0 # If digits don't match, we can't match them
        
    # Check extra text (e.g. "Мирзо Улугбек" in "6 Мирзо Улугбек")
    g_text = re.sub(r'\d+', '', g).strip().replace("-", " ")
    if g_text:
        # Convert to a common latin base for comparison
        g_lat = cyrillic_to_latin(g_text)
        rc_lat = cyrillic_to_latin(re.sub(r'\d+', '', rc).strip())
        rg_lat = cyrillic_to_latin(re.sub(r'\d+', '', rg).strip().replace("_", " "))
        
        g_words = g_lat.split()
        found = False
        for gw in g_words:
            if len(gw) > 2: # only check substantive words
                if gw in rc_lat or gw in rg_lat:
                    found = True
                    break
        
        if not found:
            return 1
        else:
            return 2
            
    return 2

print("Eval MIRZO ULUGBEK_41, class=6, req=6 Мирзо-Улугбек (Expected 2):", _evaluate_class_match("MIRZO ULUGBEK_41", "6", "6 Мирзо-Улугбек"))
print("Eval PRESIDENT_41, class=6, req=6 Мирзо-Улугбек (Expected 1):", _evaluate_class_match("PRESIDENT_41", "6", "6 Мирзо-Улугбек"))
print("Eval PRESIDENT_41, class=6, req=6 (Expected 2):", _evaluate_class_match("PRESIDENT_41", "6", "6"))
print("Eval JUNIOR_41, class=5, req=6 (Expected 0):", _evaluate_class_match("JUNIOR_41", "5", "6"))
