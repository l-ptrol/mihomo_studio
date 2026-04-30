package proxy

import (
	"fmt"
	"regexp"
	"strings"
)

// InsertProxyLogic вставляет прокси в секцию proxies и добавляет в выбранные группы
func InsertProxyLogic(content, proxyName, proxyYAML string, targetGroups []string) string {
	lines := strings.Split(content, "\n")
	newLines := make([]string, 0, len(lines)+20)

	getIndent := func(s string) int {
		return len(s) - len(strings.TrimLeft(s, " \t"))
	}

	targetSet := make(map[string]bool)
	for _, g := range targetGroups {
		targetSet[g] = true
	}

	inGroupSection := false
	var currentGroupName string
	inProxiesList := false
	proxiesListIndent := -1
	insertedInGroup := make(map[string]bool)

	// Сначала вставляем блок прокси в секцию proxies
	proxyInserted := false
	proxyLines := strings.Split(proxyYAML, "\n")

	for i, line := range lines {
		stripped := strings.TrimSpace(line)
		indent := getIndent(line)
		isNewGroup := strings.HasPrefix(stripped, "- name:")

		// Вставка в proxies
		if !proxyInserted && strings.HasPrefix(stripped, "proxies:") {
			newLines = append(newLines, line)
			for _, pl := range proxyLines {
				newLines = append(newLines, "  "+pl)
			}
			proxyInserted = true
			continue
		}

		if isNewGroup {
			if inProxiesList && targetSet[currentGroupName] && !insertedInGroup[currentGroupName] {
				prefix := strings.Repeat(" ", proxiesListIndent+2)
				newLines = append(newLines, prefix+`- "`+proxyName+`"`)
				insertedInGroup[currentGroupName] = true
			}
			inProxiesList = false
		}

		if stripped == "proxy-groups:" {
			inGroupSection = true
		} else if inGroupSection && indent == 0 && stripped != "" && !strings.HasPrefix(stripped, "#") {
			inGroupSection = false
			inProxiesList = false
			currentGroupName = ""
		}

		if inGroupSection {
			if isNewGroup {
				rawName := strings.SplitN(stripped, ":", 2)[1]
				rawName = strings.TrimSpace(rawName)
				rawName = strings.Trim(rawName, "'\"")
				currentGroupName = rawName
			}

			if targetSet[currentGroupName] && strings.HasPrefix(stripped, "proxies:") {
				// Inline list ["A", "B"]
				if strings.Contains(line, "[") && strings.HasSuffix(strings.TrimSpace(line), "]") {
					start := strings.Index(line, "[")
					end := strings.LastIndex(line, "]")
					if start != -1 && end != -1 {
						inner := line[start+1 : end]
						if !strings.Contains(inner, `"`+proxyName+`"`) && !strings.Contains(inner, `'`+proxyName+`'`) {
							sep := ", "
							if strings.TrimSpace(inner) == "" {
								sep = ""
							}
							newContent := inner + sep + `"` + proxyName + `"`
							newLine := line[:start+1] + newContent + line[end:]
							newLines = append(newLines, newLine)
							insertedInGroup[currentGroupName] = true
							continue
						} else {
							newLines = append(newLines, line)
							insertedInGroup[currentGroupName] = true
							continue
						}
					}
				}

				inProxiesList = true
				proxiesListIndent = indent
				newLines = append(newLines, line)
				continue
			}

			if inProxiesList {
				if stripped == "" || strings.HasPrefix(stripped, "#") {
					newLines = append(newLines, line)
					continue
				}

				// Проверка на дубликат, чтобы не добавлять прокси дважды
				if stripped == "- "+proxyName || stripped == "- \""+proxyName+"\"" || stripped == "- '"+proxyName+"'" {
					insertedInGroup[currentGroupName] = true
				}

				if (strings.Contains(stripped, "DIRECT") || strings.Contains(stripped, "REJECT")) && !insertedInGroup[currentGroupName] {
					prefix := strings.Repeat(" ", indent)
					newLines = append(newLines, prefix+`- "`+proxyName+`"`)
					insertedInGroup[currentGroupName] = true
				}

				if indent <= proxiesListIndent {
					if !insertedInGroup[currentGroupName] {
						prefix := strings.Repeat(" ", proxiesListIndent+2)
						newLines = append(newLines, prefix+`- "`+proxyName+`"`)
						insertedInGroup[currentGroupName] = true
					}
					inProxiesList = false
				}
			}
		}

		newLines = append(newLines, line)

		// Для последней строки
		if i == len(lines)-1 && inProxiesList && targetSet[currentGroupName] && !insertedInGroup[currentGroupName] {
			prefix := strings.Repeat(" ", proxiesListIndent+2)
			newLines = append(newLines, prefix+`- "`+proxyName+`"`)
		}
	}

	// Если proxies не найдена — добавляем в конец
	if !proxyInserted {
		newLines = append(newLines, "proxies:")
		for _, pl := range proxyLines {
			newLines = append(newLines, "  "+pl)
		}
	}

	return strings.Join(newLines, "\n")
}

// ReplaceProxyBlock заменяет блок прокси с targetName на новый YAML
func ReplaceProxyBlock(content, targetName, newYAML string) string {
	lines := strings.Split(content, "\n")
	newContentLines := make([]string, 0, len(lines))

	inProxies := false
	replaced := false
	namePattern := regexp.MustCompile(`^\s*-\s+name:\s*(["'])?` + regexp.QuoteMeta(targetName) + `\1?\s*$`)

	newYAMLLines := strings.Split(newYAML, "\n")
	// Заменяем имя в первой строке нового YAML
	if len(newYAMLLines) > 0 && strings.Contains(newYAMLLines[0], "name:") {
		newYAMLLines[0] = regexp.MustCompile(`name:\s*"[^"]*"`).ReplaceAllString(newYAMLLines[0], `name: "`+targetName+`"`)
	}

	i := 0
	for i < len(lines) {
		line := lines[i]
		stripped := strings.TrimSpace(line)

		if strings.HasPrefix(stripped, "proxies:") {
			inProxies = true
			newContentLines = append(newContentLines, line)
			i++
			continue
		}

		if inProxies && line != "" && !strings.HasPrefix(line, " ") && !strings.HasPrefix(line, "\t") && !strings.HasPrefix(stripped, "#") {
			inProxies = false
		}

		if inProxies && !replaced && namePattern.MatchString(stripped) {
			indentLen := len(line) - len(strings.TrimLeft(line, " \t"))
			for _, nLine := range newYAMLLines {
				newContentLines = append(newContentLines, strings.Repeat(" ", indentLen)+nLine)
			}
			replaced = true

			i++
			for i < len(lines) {
				nextLine := lines[i]
				nextStripped := strings.TrimSpace(nextLine)
				nextIndent := len(nextLine) - len(strings.TrimLeft(nextLine, " \t"))
				if nextStripped == "" {
					i++
					continue
				}
				if nextIndent < indentLen {
					break
				}
				if nextIndent == indentLen && strings.HasPrefix(nextStripped, "-") {
					break
				}
				i++
			}
			continue
		}

		newContentLines = append(newContentLines, line)
		i++
	}

	return strings.Join(newContentLines, "\n")
}

// RenameProxy переименовывает прокси во всех местах конфига
func RenameProxy(content, oldName, newName string) string {
	escapedOld := regexp.QuoteMeta(oldName)

	// 1. Замена в определении прокси
	patternDef := `(name\s*:\s*)(["']?)` + escapedOld + `\2`
	content = regexp.MustCompile(patternDef).ReplaceAllString(content, `${1}"`+newName+`"`)

	// 2. Замена в списках proxy-groups
	patternList := `(-\s+)(["']?)` + escapedOld + `\2`
	content = regexp.MustCompile(patternList).ReplaceAllString(content, `${1}"`+newName+`"`)

	// 3. Замена в inline lists
	patternInline := `([\[,]\s*)(["']?)` + escapedOld + `\2(\s*[\],])`
	content = regexp.MustCompile(patternInline).ReplaceAllString(content, fmt.Sprintf(`${1}${2}%s${2}${3}`, newName))

	return content
}

// DeleteProxy удаляет прокси из конфига
func DeleteProxy(content, proxyName string) string {
	lines := strings.Split(content, "\n")
	nls := make([]string, 0, len(lines))
	inP := false
	delB := false
	bInd := -1

	escapedName := regexp.QuoteMeta(proxyName)
	namePattern := regexp.MustCompile(`^\s*-\s+name:\s*["']?` + escapedName + `["']?\s*$`)
	listPattern := regexp.MustCompile(`^\s+-\s+(?:"` + escapedName + `"|'` + escapedName + `'|` + escapedName + `)\s*$`)

	for _, l := range lines {
		stripped := strings.TrimSpace(l)

		if strings.HasPrefix(stripped, "proxies:") && !strings.Contains(stripped, "[") {
			inP = true
			nls = append(nls, l)
			continue
		}
		if inP && l != "" && !strings.HasPrefix(l, " ") && !strings.HasPrefix(l, "\t") && !strings.HasPrefix(stripped, "#") {
			inP = false
			delB = false
		}

		if inP {
			if namePattern.MatchString(l) {
				delB = true
				bInd = len(l) - len(strings.TrimLeft(l, " \t"))
				continue
			} else if delB {
				ci := len(l) - len(strings.TrimLeft(l, " \t"))
				if stripped == "" || ci > bInd {
					continue
				} else {
					delB = false
				}
			}
		}

		if delB {
			continue
		}

		// Inline list
		if strings.Contains(l, "[") && strings.Contains(l, "]") && strings.Contains(l, "proxies:") {
			// Не трогаем, обрабатываем отдельно
		}

		// Удаление из списков групп
		if listPattern.MatchString(l) {
			continue
		}

		// Удаление из inline списков
		if strings.Contains(l, "[") && strings.Contains(l, "]") {
			start := strings.Index(l, "[")
			end := strings.LastIndex(l, "]")
			if start != -1 && end != -1 {
				pre := l[:start+1]
				suf := l[end:]
				mid := l[start+1 : end]
				parts := strings.Split(mid, ",")
				var res []string
				changed := false
				for _, p := range parts {
					clean := strings.TrimSpace(strings.Trim(p, "\"'"))
					if clean == proxyName {
						changed = true
					} else {
						res = append(res, p)
					}
				}
				if changed {
					nls = append(nls, pre+strings.Join(res, ",")+suf)
					continue
				}
			}
		}

		nls = append(nls, l)
	}

	return strings.Join(nls, "\n")
}

// GetProxiesList извлекает список имён прокси из конфига
func GetProxiesList(content string) []string {
	lines := strings.Split(content, "\n")
	var prs []string
	inP := false
	nameRe := regexp.MustCompile(`^\s+-\s+name:\s+(.*)`)

	for _, l := range lines {
		stripped := strings.TrimSpace(l)
		if strings.HasPrefix(stripped, "proxies:") {
			inP = true
			continue
		}
		if inP && l != "" && !strings.HasPrefix(l, " ") && !strings.HasPrefix(l, "\t") && !strings.HasPrefix(stripped, "#") {
			inP = false
		}
		if inP {
			if m := nameRe.FindStringSubmatch(l); m != nil {
				name := strings.TrimSpace(m[1])
				name = strings.Trim(name, "\"'")
				prs = append(prs, name)
			}
		}
	}
	return prs
}

// GetGroupsList извлекает список групп из конфига
func GetGroupsList(content string) []string {
	lines := strings.Split(content, "\n")
	var grps []string
	inG := false
	nameRe := regexp.MustCompile(`^\s+-\s+name:\s+(.*)`)

	for _, l := range lines {
		stripped := strings.TrimSpace(l)
		if strings.HasPrefix(stripped, "proxy-groups:") {
			inG = true
			continue
		}
		if inG && l != "" && !strings.HasPrefix(l, " ") && !strings.HasPrefix(l, "\t") && !strings.HasPrefix(stripped, "#") {
			inG = false
		}
		if inG {
			if m := nameRe.FindStringSubmatch(l); m != nil {
				name := strings.TrimSpace(m[1])
				name = strings.Trim(name, "\"'")
				grps = append(grps, name)
			}
		}
	}
	return grps
}
