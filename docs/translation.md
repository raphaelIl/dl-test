```shell
# 메시지 템플릿 최신화
pybabel extract -F babel.cfg -o messages.pot .

# 중국어 번역 파일 생성
pybabel init -i messages.pot -d translations -l zh

# 번역 파일 컴파일
pybabel compile -d translations
```
