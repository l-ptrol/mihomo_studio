# /opt/scripts/mihomo-studio/yaml_utils.py
import yaml

def insert_proxy_logic(data, proxy_name, target_groups):
    """
    Inserts a proxy into the specified proxy groups in the given data structure.
    """
    if 'proxy-groups' not in data or not isinstance(data['proxy-groups'], list):
        return data

    for group in data['proxy-groups']:
        if isinstance(group, dict) and group.get('name') in target_groups:
            if 'proxies' not in group or group['proxies'] is None:
                group['proxies'] = []
            
            if proxy_name not in group['proxies']:
                # Find a suitable insertion point (e.g., before DIRECT or REJECT)
                # This is a simple approach; more complex logic might be needed for specific ordering.
                inserted = False
                for keyword in ['DIRECT', 'REJECT']:
                    if keyword in group['proxies']:
                        index = group['proxies'].index(keyword)
                        group['proxies'].insert(index, proxy_name)
                        inserted = True
                        break
                if not inserted:
                    group['proxies'].append(proxy_name)
    
    return data


def replace_proxy_block(data, target_name, new_proxy_data):
    """
    Replaces a proxy definition in the 'proxies' list.
    """
    if 'proxies' not in data or not isinstance(data['proxies'], list):
        return data

    for i, proxy in enumerate(data['proxies']):
        if isinstance(proxy, dict) and proxy.get('name') == target_name:
            # Ensure the name in the new data matches the target name
            new_proxy_data['name'] = target_name
            data['proxies'][i] = new_proxy_data
            break
            
    return data