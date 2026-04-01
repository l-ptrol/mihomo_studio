package parser

import (
	"net/url"
	"regexp"
	"strings"
)

type ProxyResult struct {
	YAML string `json:"yaml"`
	Name string `json:"name"`
}

func ParseVLESS(link, customName string) (*ProxyResult, error) {
	if !strings.HasPrefix(link, "vless://") {
		return nil, &ParseError{Msg: "Link error"}
	}

	main := link[8:]
	name := "VLESS"

	if customName != "" {
		name = customName
	} else if idx := strings.Index(main, "#"); idx != -1 {
		decoded, _ := url.QueryUnescape(main[idx+1:])
		name = strings.TrimSpace(decoded)
		main = main[:idx]
	}

	// Убираем спецсимволы из имени
	re := regexp.MustCompile(`[\[\]\{\}\"\']`)
	name = re.ReplaceAllString(name, "")

	// Разделяем user@server:port и параметры
	queryIdx := strings.Index(main, "?")
	userSrv := main
	queryParams := url.Values{}
	if queryIdx != -1 {
		userSrv = main[:queryIdx]
		queryParams, _ = url.ParseQuery(main[queryIdx+1:])
	}

	atIdx := strings.LastIndex(userSrv, "@")
	if atIdx == -1 {
		return nil, &ParseError{Msg: "No UUID"}
	}
	uuid := userSrv[:atIdx]
	srvPort := userSrv[atIdx+1:]

	var server, port string
	if strings.Contains(srvPort, "]:") {
		// IPv6
		lastColon := strings.LastIndex(srvPort, ":")
		server = srvPort[:lastColon]
		server = strings.ReplaceAll(server, "[", "")
		server = strings.ReplaceAll(server, "]", "")
		port = srvPort[lastColon+1:]
	} else if colonIdx := strings.Index(srvPort, ":"); colonIdx != -1 {
		server = srvPort[:colonIdx]
		port = srvPort[colonIdx+1:]
	} else {
		return nil, &ParseError{Msg: "No Port"}
	}

	get := func(k string) string {
		vals := queryParams[k]
		if len(vals) > 0 {
			return vals[0]
		}
		return ""
	}

	var y []string
	y = append(y, `- name: "`+name+`"`)
	y = append(y, "  type: vless")
	y = append(y, "  server: "+server)
	y = append(y, "  port: "+port)
	y = append(y, "  uuid: "+uuid)
	y = append(y, "  udp: true")

	network := get("type")
	if network == "" {
		network = "tcp"
	}
	y = append(y, "  network: "+network)

	if flow := get("flow"); flow != "" {
		y = append(y, "  flow: "+flow)
	}

	if security := get("security"); security != "" {
		y = append(y, "  tls: true")
		if security == "reality" {
			y = append(y, "  servername: "+get("sni"))
			fp := get("fp")
			if fp == "" {
				fp = "chrome"
			}
			y = append(y, "  client-fingerprint: "+fp)
			y = append(y, "  reality-opts:")
			y = append(y, "    public-key: "+get("pbk"))
			if sid := get("sid"); sid != "" {
				y = append(y, "    short-id: "+sid)
			}
		} else {
			if sni := get("sni"); sni != "" {
				y = append(y, "  servername: "+sni)
			}
			if fp := get("fp"); fp != "" {
				y = append(y, "  client-fingerprint: "+fp)
			}
			if alpn := get("alpn"); alpn != "" {
				alpn = strings.ReplaceAll(alpn, ",", `", "`)
				y = append(y, `  alpn: ["`+alpn+`"]`)
			}
		}
	}

	if network == "ws" {
		y = append(y, "  ws-opts:")
		if path := get("path"); path != "" {
			y = append(y, "    path: "+path)
		}
		if host := get("host"); host != "" {
			y = append(y, "    headers:")
			y = append(y, "      Host: "+host)
		}
	} else if network == "grpc" {
		if serviceName := get("serviceName"); serviceName != "" {
			y = append(y, "  grpc-opts:")
			y = append(y, "    grpc-service-name: "+serviceName)
		}
	}

	return &ProxyResult{
		YAML: strings.Join(y, "\n"),
		Name: name,
	}, nil
}

type ParseError struct {
	Msg string
}

func (e *ParseError) Error() string {
	return e.Msg
}
