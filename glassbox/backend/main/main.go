package main

import "log"

func main() {
	if err := runagent(); err != nil {
		log.Println(err)
	}
}
