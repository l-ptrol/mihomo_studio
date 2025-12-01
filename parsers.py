# /opt/scripts/mihomo-studio/parsers.py
import urllib.parse
import re
import yaml


def parse_vless(link, custom_name=None):
    try:
        if not link.startswith("vless://"): return None, "Link error"
        main = link[8:]
        name = "VLESS"
        if custom_name:
            name = custom_name
        elif '#' in main:
            main, n = main.split('#', 1)
            name = urllib.parse.unquote(n).strip()

        name = re.sub(r'[\[\]\{\}\"\']', '', name)
        user_srv = main.split('?')[0]
        params = urllib.parse.parse_qs(main.split('?')[1]) if '?' in main else {}

        if '@' in user_srv:
            uuid, srv_port = user_srv.split('@', 1)
        else:
            return None, "No UUID"

        if ':' in srv_port:
            if ']' in srv_port:
                srv, port = srv_port.rsplit(':', 1)
                srv = srv.replace('[', '').replace(']', '')
            else:
                srv, port = srv_port.split(':')
        else:
            return None, "No Port"

        def get(k):
            return params.get(k, [''])[0]

        proxy = {
            'name': name,
            'type': 'vless',
            'server': srv,
            'port': int(port),
            'uuid': uuid,
            'udp': True,
            'network': get('type') or 'tcp'
        }
        if get('flow'): proxy['flow'] = get('flow')
        
        if get('security'):
            proxy['tls'] = True
            if get('security') == 'reality':
                proxy['servername'] = get('sni')
                proxy['client-fingerprint'] = get('fp') or 'chrome'
                proxy['reality-opts'] = {'public-key': get('pbk')}
                if get('sid'): proxy['reality-opts']['short-id'] = get('sid')
            else:
                if get('sni'): proxy['servername'] = get('sni')
                if get('fp'): proxy['client-fingerprint'] = get('fp')
                if get('alpn'): proxy['alpn'] = [x.strip() for x in get('alpn').split(',')]

        if get('type') == 'ws':
            proxy['ws-opts'] = {}
            if get('path'): proxy['ws-opts']['path'] = get('path')
            if get('host'): proxy['ws-opts']['headers'] = {'Host': get('host')}
        elif get('type') == 'grpc' and get('serviceName'):
            proxy['grpc-opts'] = {'grpc-service-name': get('serviceName')}

        return {"yaml": yaml.dump([proxy], allow_unicode=True, sort_keys=False), "name": name}, None
    except Exception as e:
        return None, str(e)


def parse_wireguard(config_text, custom_name=None):
    try:
        conf = {"interface": {}, "peer": {}}
        section = None

        for line in config_text.splitlines():
            line = line.split('#')[0].split(';')[0].strip()
            if not line: continue

            if line.startswith('[') and line.endswith(']'):
                s_name = line[1:-1].lower()
                if s_name == 'interface' or s_name == 'peer':
                    section = s_name
                else:
                    section = None
                continue

            if section and '=' in line:
                key, val = line.split('=', 1)
                conf[section][key.strip().lower()] = val.strip()

        iface = conf['interface']
        peer = conf['peer']

        if not iface or not peer:
            return None, "Invalid WireGuard config: missing Interface or Peer"

        endpoint = peer.get('endpoint', '')
        if not endpoint: return None, "No Endpoint found"

        if ']:' in endpoint:
            server = endpoint.split(']:')[0][1:]
            port = endpoint.split(']:')[1]
        elif ':' in endpoint:
            server, port = endpoint.rsplit(':', 1)
        else:
            return None, "Invalid Endpoint format"

        name = "WireGuard"
        if custom_name:
            name = custom_name
        else:
            first_line = config_text.splitlines()[0].strip()
            if first_line.startswith('#') and len(first_line) > 2:
                name = first_line[1:].strip()
            else:
                name = f"WG_{server}"

        address_raw = iface.get('address', '')
        if not address_raw: return None, "No Address found"

        ips = [x.strip() for x in address_raw.split(',')]
        ip_v4 = None
        ip_v6 = None

        for ip in ips:
            clean_ip = ip.split('/')[0]
            if ':' in clean_ip:
                if not ip_v6: ip_v6 = clean_ip
            else:
                if not ip_v4: ip_v4 = clean_ip

        if not ip_v4 and not ip_v6:
            return None, "No valid IP address found"

        proxy = {
            'name': name,
            'type': 'wireguard',
            'server': server,
            'port': int(port),
            'udp': True
        }
        if ip_v4: proxy['ip'] = ip_v4
        if ip_v6: proxy['ipv6'] = ip_v6
        if iface.get('privatekey'): proxy['private-key'] = iface.get('privatekey')
        if peer.get('publickey'): proxy['public-key'] = peer.get('publickey')
        if peer.get('presharedkey'): proxy['pre-shared-key'] = peer.get('presharedkey')
        if iface.get('dns'): proxy['dns'] = [d.strip() for d in iface.get('dns').split(',')]
        if iface.get('mtu'): proxy['mtu'] = int(iface.get('mtu'))

        amnezia_keys = ['jc', 'jmin', 'jmax', 's1', 's2', 'h1', 'h2', 'h3', 'h4']
        amn_opts = {}
        for k in amnezia_keys:
            if k in iface and iface[k].isdigit():
                amn_opts[k] = int(iface[k])
        
        if amn_opts:
            proxy['amnezia-wg-option'] = amn_opts

        if peer.get('allowedips'):
            proxy['allowed-ips'] = [x.strip() for x in peer.get('allowedips').split(',')]
        
        if peer.get('persistentkeepalive'):
            proxy['persistent-keepalive'] = int(peer.get('persistentkeepalive'))

        return {"yaml": yaml.dump([proxy], allow_unicode=True, sort_keys=False), "name": name}, None

    except Exception as e:
        return None, str(e)