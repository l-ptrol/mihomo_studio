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
                # Логика вставки перед DIRECT/REJECT для красоты
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

    # 1. Добавляем в proxy-providers
    if 'proxy-providers' not in data:
        # Если секции нет, создаем её
        # Чтобы вставить ПЕРЕД 'proxies', ищем индекс ключа 'proxies'
        keys = list(data.keys())
        insert_idx = len(keys)  # По умолчанию в конец

        if 'proxies' in keys:
            insert_idx = keys.index('proxies')

        # Создаем новый словарь для провайдеров
        # Используем insert(позиция, ключ, значение)
        # data здесь - это CommentedMap из ruamel.yaml
        try:
            data.insert(insert_idx, 'proxy-providers', {provider_name: provider_data})
        except AttributeError:
            # Если вдруг data это обычный dict (fallback)
            data['proxy-providers'] = {provider_name: provider_data}

    else:
        # Если секция уже есть, просто добавляем туда новый провайдер
        # Порядок внутри proxy-providers не так важен, но добавится в конец секции
        if data['proxy-providers'] is None:
            data['proxy-providers'] = {}
        data['proxy-providers'][provider_name] = provider_data

    # 2. Добавляем ссылку на провайдер в поле 'use' групп
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
            # Ensure the name in the new data matches the target name
            new_proxy_data['name'] = target_name
            data['proxies'][i] = new_proxy_data
            break

    return data