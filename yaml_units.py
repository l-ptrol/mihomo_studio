# /opt/scripts/mihomo-studio/yaml_units.py
# -*- coding: utf-8 -*-

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


def insert_provider_logic(data, provider_name, provider_data, target_groups):
    """
    Inserts a proxy provider into 'proxy-providers' (BEFORE 'proxies')
    and adds it to the 'use' list of target groups.
    """
    keys = list(data.keys())
    insert_idx = len(keys)

    if 'proxies' in keys:
        insert_idx = keys.index('proxies')

    if 'proxy-providers' not in data:
        try:
            data.insert(insert_idx, 'proxy-providers', {provider_name: provider_data})
        except AttributeError:
            data['proxy-providers'] = {provider_name: provider_data}
    else:
        if data['proxy-providers'] is None:
            data['proxy-providers'] = {}
        data['proxy-providers'][provider_name] = provider_data

    if 'proxy-groups' in data and isinstance(data['proxy-groups'], list):
        for group in data['proxy-groups']:
            if isinstance(group, dict) and group.get('name') in target_groups:
                if 'use' not in group or group['use'] is None:
                    group['use'] = []

                if provider_name not in group['use']:
                    group['use'].append(provider_name)

    return data


def replace_proxy_block(data, target_name, new_proxy_data):
    """
    Replaces a proxy definition in the 'proxies' list.
    """
    if 'proxies' not in data or not isinstance(data['proxies'], list):
        return data

    for i, proxy in enumerate(data['proxies']):
        if isinstance(proxy, dict) and proxy.get('name') == target_name:
            new_proxy_data['name'] = target_name
            data['proxies'][i] = new_proxy_data
            break

    return data


def delete_item_logic(data, item_name):
    """
    Deletes a proxy or provider references from everywhere.
    """
    # 1. Удаляем из списка proxies, если есть
    if 'proxies' in data and isinstance(data['proxies'], list):
        # Создаем новый список, исключая удаляемый элемент
        # (ruamel.yaml сохранит комментарии для остальных элементов)
        original_len = len(data['proxies'])
        # Ищем индекс для удаления, чтобы не ломать структуру
        idx_to_remove = -1
        for i, p in enumerate(data['proxies']):
            if isinstance(p, dict) and p.get('name') == item_name:
                idx_to_remove = i
                break
        if idx_to_remove != -1:
            del data['proxies'][idx_to_remove]

    # 2. Удаляем из proxy-providers, если есть
    if 'proxy-providers' in data and isinstance(data['proxy-providers'], dict):
        if item_name in data['proxy-providers']:
            del data['proxy-providers'][item_name]

    # 3. Чистим группы
    if 'proxy-groups' in data and isinstance(data['proxy-groups'], list):
        for group in data['proxy-groups']:
            if not isinstance(group, dict): continue

            # Удаляем из списка 'proxies' внутри группы
            if 'proxies' in group and isinstance(group['proxies'], list):
                if item_name in group['proxies']:
                    group['proxies'].remove(item_name)

            # Удаляем из списка 'use' внутри группы
            if 'use' in group and isinstance(group['use'], list):
                if item_name in group['use']:
                    group['use'].remove(item_name)

                # ВАЖНО: Если список use стал пустым, удаляем ключ use целиком
                if len(group['use']) == 0:
                    del group['use']

    return data