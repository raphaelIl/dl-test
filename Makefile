VERSION = $(shell date +%Y%m%d)
DOCKER_HUB_USER = raphael1021
IMAGE_NAME = dl-test
CONTAINER_NAME = video-downloader

# Redis를 제외한 앱 서비스 목록
APP_SERVICES = grab-video cloudflared

.PHONY: deploy restart restart-app clean-all clean-image clean build build-push push

# 로컬 빌드 → 앱만 재생성 (Redis 유지, push 없음) — 일상 배포용
deploy: build
	@echo "앱 서비스만 재생성 (Redis 유지)"
	docker compose up -d --no-deps --force-recreate $(APP_SERVICES)
	caffeinate -i docker compose logs -f $(APP_SERVICES)

# 앱 서비스만 단순 재시작 (이미지 변경 없이 컨테이너만 restart)
restart-app:
	@echo "앱 서비스만 재시작 (Redis 유지)"
	docker compose restart $(APP_SERVICES)

# 전체 재시작 (Redis 포함) — 초기 셋업 또는 Redis 설정 변경 시
restart: clean
	@echo "docker compose up -d"
	VERSION=$(VERSION) DOCKER_HUB_USER=$(DOCKER_HUB_USER) IMAGE_NAME=$(IMAGE_NAME) docker compose up -d
	caffeinate -i docker compose logs -f

clean:
	@echo "docker compose down --remove-orphans"
	VERSION=$(VERSION) DOCKER_HUB_USER=$(DOCKER_HUB_USER) IMAGE_NAME=$(IMAGE_NAME) docker compose down --remove-orphans || true

# 로컬 빌드만 (push 없음)
build:
	@echo "빌드 시작: $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION)"
	docker build -t $(DOCKER_HUB_USER)/$(IMAGE_NAME):$(VERSION) -t $(DOCKER_HUB_USER)/$(IMAGE_NAME):latest .

# 빌드 + Docker Hub push
build-push: build push

push:
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
