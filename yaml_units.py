# /opt/scripts/mihomo-studio/yaml_utils.py
import re

def insert_proxy_logic(content, proxy_name, target_groups):
    lines = content.splitlines()
    new_lines = []

    def get_indent(s):
        return len(s) - len(s.lstrip())

    in_group_section = False
    current_group_name = None
    in_proxies_list = False
    proxies_list_indent = -1
    inserted_in_group = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        indent = get_indent(line)
        is_new_group = stripped.startswith('- name:')

        if is_new_group:
            if in_proxies_list and current_group_name in target_groups and current_group_name not in inserted_in_group:
                prefix = " " * (proxies_list_indent + 2)
                new_lines.append(prefix + '- "' + proxy_name + '"')
                inserted_in_group.add(current_group_name)
            in_proxies_list = False

        if stripped.startswith('proxy-groups:'):
            in_group_section = True
        elif in_group_section and indent == 0 and stripped and not stripped.startswith('#'):
            in_group_section = False
            in_proxies_list = False
            current_group_name = None

        if in_group_section:
            if is_new_group:
                raw_name = stripped.split(':', 1)[1].strip()
                current_group_name = raw_name.strip("'").strip('"')

            if current_group_name in target_groups and stripped.startswith('proxies:'):
                if '[' in stripped and stripped.rstrip().endswith(']'):
                    start = line.find('[')
                    end = line.rfind(']')
                    if start != -1 and end != -1:
                        content_inner = line[start + 1:end]
                        if proxy_name not in content_inner:
                            sep = ", " if content_inner.strip() else ""
                            new_content = content_inner + sep + f'"{proxy_name}"'
                            new_line = line[:start + 1] + new_content + line[end:]
                            new_lines.append(new_line)
                            inserted_in_group.add(current_group_name)
                            continue
                        else:
                            new_lines.append(line)
                            inserted_in_group.add(current_group_name)
                            continue

                in_proxies_list = True
                proxies_list_indent = indent
                new_lines.append(line)
                continue

            if in_proxies_list:
                if not stripped or stripped.startswith('#'):
                    new_lines.append(line)
                    continue
                if ('DIRECT' in stripped or 'REJECT' in stripped) and current_group_name not in inserted_in_group:
                    prefix = " " * indent
                    new_lines.append(prefix + '- "' + proxy_name + '"')
                    inserted_in_group.add(current_group_name)

                if indent <= proxies_list_indent:
                    if current_group_name not in inserted_in_group:
                        prefix = " " * (proxies_list_indent + 2)
                        new_lines.append(prefix + '- "' + proxy_name + '"')
                        inserted_in_group.add(current_group_name)
                    in_proxies_list = False

        new_lines.append(line)

    if in_proxies_list and current_group_name in target_groups and current_group_name not in inserted_in_group:
        prefix = " " * (proxies_list_indent + 2)
        new_lines.append(prefix + '- "' + proxy_name + '"')

    return "\n".join(new_lines)


def replace_proxy_block(content, target_name, new_yaml_lines):
    lines = content.splitlines()
    new_content_lines = []

    in_proxies = False
    found_target = False
    replaced = False

    name_pattern = re.compile(r'^\s*-\s+name:\s*(["\'])?' + re.escape(target_name) + r'(\1)?\s*$')

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith('proxies:'):
            in_proxies = True
            new_content_lines.append(line)
            i += 1
            continue

        if in_proxies and line and not line.startswith(' ') and not line.startswith('\t') and not line.startswith('#'):
            in_proxies = False

        if in_proxies and not replaced:
            if name_pattern.match(stripped):
                indent_len = len(line) - len(line.lstrip())
                if new_yaml_lines and "name:" in new_yaml_lines[0]:
                    new_yaml_lines[0] = re.sub(r'name:\s*".*"', f'name: "{target_name}"', new_yaml_lines[0])

                for n_line in new_yaml_lines:
                    new_content_lines.append(" " * indent_len + n_line)

                replaced = True
                found_target = True

                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    next_stripped = next_line.strip()
                    next_indent = len(next_line) - len(next_line.lstrip())
                    if not next_stripped:
                        i += 1
                        continue
                    if next_indent < indent_len: break
                    if next_indent == indent_len and next_stripped.startswith('-'): break
                    i += 1
                continue

        new_content_lines.append(line)
        i += 1

    return "\n".join(new_content_lines)