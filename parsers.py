# /opt/scripts/mihomo-studio/parsers.py
import urllib.parse
import re
import json


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

        y = ['- name: "' + name + '"', '  type: vless', '  server: ' + srv, '  port: ' + port, '  uuid: ' + uuid,
             '  udp: true']
        y.append('  network: ' + (get('type') or 'tcp'))
        if get('flow'): y.append('  flow: ' + get('flow'))
        if get('security'):
            y.append('  tls: true')
            if get('security') == 'reality':
                y.extend(['  servername: ' + get('sni'), '  client-fingerprint: ' + (get('fp') or 'chrome'),
                          '  reality-opts:', '    public-key: ' + get('pbk')])
                if get('sid'): y.append('    short-id: ' + get('sid'))
            else:
                if get('sni'): y.append('  servername: ' + get('sni'))
                if get('fp'): y.append('  client-fingerprint: ' + get('fp'))
                if get('alpn'):
                    av = get("alpn").replace(",", '", "')
                    y.append('  alpn: ["' + av + '"]')
        if get('type') == 'ws':
            y.append('  ws-opts:')
            if get('path'): y.append('    path: ' + get('path'))
            if get('host'): y.extend(['    headers:', '      Host: ' + get('host')])
        elif get('type') == 'grpc' and get('serviceName'):
            y.extend(['  grpc-opts:', '    grpc-service-name: ' + get('serviceName')])
        return {"yaml": "\n".join(y), "name": name}, None
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

        y = []
        y.append(f'- name: "{name}"')
        y.append(f'  type: wireguard')
        y.append(f'  server: {server}')
        y.append(f'  port: {port}')

        if ip_v4: y.append(f'  ip: {ip_v4}')
        if ip_v6: y.append(f'  ipv6: {ip_v6}')

        pk = iface.get('privatekey')
        if pk: y.append(f'  private-key: {pk}')

        pubk = peer.get('publickey')
        if pubk: y.append(f'  public-key: {pubk}')

        psk = peer.get('presharedkey')
        if psk: y.append(f'  pre-shared-key: {psk}')

        dns_raw = iface.get('dns')
        if dns_raw:
            dns_list = [d.strip() for d in dns_raw.split(',')]
            y.append(f'  dns: {json.dumps(dns_list)}')

        mtu = iface.get('mtu')
        if mtu: y.append(f'  mtu: {mtu}')

        y.append('  udp: true')

        amnezia_keys = ['jc', 'jmin', 'jmax', 's1', 's2', 'h1', 'h2', 'h3', 'h4']
        amn_opts = {}
        for k in amnezia_keys:
            if k in iface:
                val = iface[k]
                if val.isdigit():
                    amn_opts[k] = int(val)

        if amn_opts:
            y.append('  amnezia-wg-option:')
            for k, v in amn_opts.items():
                y.append(f'    {k}: {v}')

        allowed = peer.get('allowedips')
        if allowed:
            al_list = [x.strip() for x in allowed.split(',')]
            y.append(f'  allowed-ips: {json.dumps(al_list)}')

        ka = peer.get('persistentkeepalive')
        if ka:
            y.append(f'  persistent-keepalive: {ka}')

        return {"yaml": "\n".join(y), "name": name}, None

    except Exception as e:
        return None, str(e)