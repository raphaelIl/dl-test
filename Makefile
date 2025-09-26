VERSION = $(shell date +%Y%m%d)
DOCKER_HUB_USER = raphael1021
IMAGE_NAME = dl-test
CONTAINER_NAME = video-downloader

.PHONY: build-restart restart clean-all clean-image clean latest-build build

build-restart: latest-build restart

restart: clean
	@echo "docker compose up -d"
	VERSION=$(VERSION) DOCKER_HUB_USER=$(DOCKER_HUB_USER) IMAGE_NAME=$(IMAGE_NAME) docker compose up -d
	@echo "caffeinate -i docker compose up"
	caffeinate -i docker compose up

clean:
	@echo "docker compose down --remove-orphans"
	VERSION=$(VERSION) DOCKER_HUB_USER=$(DOCKER_HUB_USER) IMAGE_NAME=$(IMAGE_NAME) docker compose down --remove-orphans || true

latest-build:
	@echo "빌드 시작: $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest"
	docker build -t $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest .
	@echo "푸시 시작: $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest"
	docker push $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest

build:
	@echo "빌드 시작: $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)"
	docker build -t $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION) -t $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest .
	@echo "푸시 시작: $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)"
	docker push $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)
	@echo "푸시 시작: $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest"
	docker push $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest

clean-all:
	@echo "docker compose down"
	docker compose down
	@echo "docker rmi $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)"
	docker rmi $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)
	@echo "docker rmi $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest"
	docker rmi $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest
	@echo "docker system prune -f"
	docker system prune -f
	@echo "docker volume prune -f"
	docker volume prune -f
	@echo "docker network prune -f"
	docker network prune -f

clean-image:
	docker rmi -f $(docker images | sed 1d | awk '{print $3}')

#
#VERSION = $(shell date +%Y%m%d)
#DOCKER_HUB_USER = raphael1021
#IMAGE_NAME = dl-test
#BUILDX_NAME = multiarch-builder
#
#.PHONY: start clean
#
## buildx 설정 (필요할 때만 수동으로 실행)
#setup-buildx:
#	@echo "buildx 빌더 확인 중..."
#	@if ! docker buildx inspect $(BUILDX_NAME) > /dev/null 2>&1; then \
#		echo "buildx 빌더 생성: $(BUILDX_NAME)"; \
#		docker buildx create --name $(BUILDX_NAME) --use; \
#	else \
#		echo "기존 buildx 빌더 사용: $(BUILDX_NAME)"; \
#		docker buildx use $(BUILDX_NAME); \
#	fi
#	docker buildx inspect --bootstrap
#
## 멀티 아키텍처 이미지 빌드 및 푸시
#start:
#	@echo "멀티 아키텍처(arm64, amd64) 빌드 및 푸시: $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)"
#	docker buildx build --platform linux/arm64,linux/amd64 \
#		-t $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION) \
#		-t $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest \
#		--push .
#	@echo "docker compose down"
#	VERSION=$(VERSION) DOCKER_HUB_USER=$(DOCKER_HUB_USER) IMAGE_NAME=$(IMAGE_NAME) docker compose down
#	@echo "docker compose up -d"
#	VERSION=$(VERSION) DOCKER_HUB_USER=$(DOCKER_HUB_USER) IMAGE_NAME=$(IMAGE_NAME) docker compose up -d
#
#clean:
#	@echo "docker compose down"
#	docker compose down
#	@echo "로컬 이미지 정리"
#	-docker rmi $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION) 2>/dev/null || true
#	-docker rmi $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest 2>/dev/null || true
#	@echo "docker system prune -f"
#	docker system prune -f
#	@echo "docker volume prune -f"
#	docker volume prune -f
#	@echo "docker network prune -f"
#	docker network prune -f
