VERSION = latest
DOCKER_HUB_USER = raphael1021
IMAGE_NAME = dl-test

.PHONY: start

start:
	@echo "docker-compose down"
	docker-compose down
	@echo "빌드 시작: $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)"
	docker build -t $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION) .
	@echo "푸시 시작: $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)"
	docker push $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)
	@echo "docker-compose up -d"
	docker-compose up -d

clean:
	@echo "docker-compose down"
	docker-compose down
	@echo "docker rmi $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)"
	docker rmi $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)
	@echo "docker system prune -f"
	docker system prune -f
	@echo "docker volume prune -f"
	docker volume prune -f
	@echo "docker network prune -f"
	docker network prune -f
