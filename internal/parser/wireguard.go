package parser

import (
	"encoding/json"
	"strconv"
	"strings"
)

func ParseWireGuard(configText, customName string) (*ProxyResult, error) {
	conf := map[string]map[string]string{
		"interface": {},
		"peer":      {},
	}
	var section string

	for _, line := range strings.Split(configText, "\n") {
		// Убираем комментарии
		if idx := strings.IndexAny(line, "#;"); idx != -1 {
			line = line[:idx]
		}
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}

		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			sName := strings.ToLower(line[1 : len(line)-1])
			if sName == "interface" || sName == "peer" {
				section = sName
			} else {
				section = ""
			}
			continue
		}

		if section != "" && strings.Contains(line, "=") {
			parts := strings.SplitN(line, "=", 2)
			key := strings.TrimSpace(strings.ToLower(parts[0]))
			val := strings.TrimSpace(parts[1])
			conf[section][key] = val
		}
	}

	iface := conf["interface"]
	peer := conf["peer"]

	if len(iface) == 0 || len(peer) == 0 {
		return nil, &ParseError{Msg: "Invalid WireGuard config: missing Interface or Peer"}
	}

	endpoint := peer["endpoint"]
	if endpoint == "" {
		return nil, &ParseError{Msg: "No Endpoint found"}
	}

	var server, port string
	if strings.Contains(endpoint, "]:") {
		parts := strings.SplitN(endpoint, "]:", 2)
		server = parts[0][1:] // убираем [
		port = parts[1]
	} else if idx := strings.LastIndex(endpoint, ":"); idx != -1 {
		server = endpoint[:idx]
		port = endpoint[idx+1:]
	} else {
		return nil, &ParseError{Msg: "Invalid Endpoint format"}
	}

	name := "WireGuard"
	if customName != "" {
		name = customName
	} else {
		lines := strings.Split(configText, "\n")
		for _, l := range lines {
			l = strings.TrimSpace(l)
			if strings.HasPrefix(l, "#") && len(l) > 2 {
				name = strings.TrimSpace(l[1:])
				break
			}
		}
		if name == "WireGuard" {
			name = "WG_" + server
		}
	}

	addressRaw := iface["address"]
	if addressRaw == "" {
		return nil, &ParseError{Msg: "No Address found"}
	}

	ips := strings.Split(addressRaw, ",")
	var ipV4, ipV6 string
	for _, ip := range ips {
		cleanIP := strings.TrimSpace(strings.Split(ip, "/")[0])
		if strings.Contains(cleanIP, ":") {
			if ipV6 == "" {
				ipV6 = cleanIP
			}
		} else {
			if ipV4 == "" {
				ipV4 = cleanIP
			}
		}
	}

	if ipV4 == "" && ipV6 == "" {
		return nil, &ParseError{Msg: "No valid IP address found"}
	}

	var y []string
	y = append(y, `- name: "`+name+`"`)
	y = append(y, "  type: wireguard")
	y = append(y, "  server: "+server)
	y = append(y, "  port: "+port)

	if ipV4 != "" {
		y = append(y, "  ip: "+ipV4)
	}
	if ipV6 != "" {
		y = append(y, "  ipv6: "+ipV6)
	}

	if pk := iface["privatekey"]; pk != "" {
		y = append(y, "  private-key: "+pk)
	}
	if pubk := peer["publickey"]; pubk != "" {
		y = append(y, "  public-key: "+pubk)
	}
	if psk := peer["presharedkey"]; psk != "" {
		y = append(y, "  pre-shared-key: "+psk)
	}

	if dnsRaw := iface["dns"]; dnsRaw != "" {
		var dnsList []string
		for _, d := range strings.Split(dnsRaw, ",") {
			dnsList = append(dnsList, strings.TrimSpace(d))
		}
		dnsJSON, _ := json.Marshal(dnsList)
		y = append(y, "  dns: "+string(dnsJSON))
	}

	if mtu := iface["mtu"]; mtu != "" {
		y = append(y, "  mtu: "+mtu)
	}

	y = append(y, "  udp: true")

	// AmneziaWG options
	stdWGKeys := map[string]bool{
		"privatekey": true, "address": true, "dns": true, "mtu": true,
		"listenport": true, "table": true, "preup": true, "postup": true,
		"predown": true, "postdown": true,
	}
	amnOpts := make(map[string]interface{})
	for k, v := range iface {
		if !stdWGKeys[k] {
			if _, err := strconv.Atoi(v); err == nil {
				val, _ := strconv.Atoi(v)
				amnOpts[k] = val
			} else if strings.HasPrefix(v, "-") {
				if _, err := strconv.Atoi(v[1:]); err == nil {
					val, _ := strconv.Atoi(v)
					amnOpts[k] = val
				} else {
					amnOpts[k] = v
				}
			} else {
				amnOpts[k] = v
			}
		}
	}

	if len(amnOpts) > 0 {
		y = append(y, "  amnezia-wg-option:")
		for k, v := range amnOpts {
			switch val := v.(type) {
			case string:
				if val == "" {
					y = append(y, "    "+k+`: ""`)
				} else {
					y = append(y, "    "+k+": "+val)
				}
			default:
				y = append(y, "    "+k+": "+strconv.Itoa(val.(int)))
			}
		}
	}

	if allowed := peer["allowedips"]; allowed != "" {
		var alList []string
		for _, x := range strings.Split(allowed, ",") {
			alList = append(alList, strings.TrimSpace(x))
		}
		alJSON, _ := json.Marshal(alList)
		y = append(y, "  allowed-ips: "+string(alJSON))
	}

	if ka := peer["persistentkeepalive"]; ka != "" {
		y = append(y, "  persistent-keepalive: "+ka)
	}

	return &ProxyResult{
		YAML: strings.Join(y, "\n"),
		Name: name,
	}, nil
}
