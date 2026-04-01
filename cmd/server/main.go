package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"github.com/l-ptrol/mhstudio-go/internal/config"
	"github.com/l-ptrol/mhstudio-go/internal/handler"
	"github.com/l-ptrol/mhstudio-go/web"
)

func main() {
	// Инициализация конфигурации
	config.Init()

	// Загрузка шаблона
	tmplData, err := web.TemplateFS.ReadFile("templates/index.html")
	if err != nil {
		log.Fatalf("Не удалось загрузить шаблон: %v", err)
	}

	// Создание хендлера
	h := handler.New()
	h.LoadTemplate(string(tmplData))

	addr := fmt.Sprintf(":%d", config.Port)
	srv := &http.Server{
		Addr:    addr,
		Handler: h,
	}

	// Graceful shutdown
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		log.Println("Остановка сервера...")
		srv.Close()
	}()

	log.Printf("Mihomo Studio v2.1 запущен на http://0.0.0.0%s", addr)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("Ошибка сервера: %v", err)
	}
}
