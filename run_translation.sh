#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import argparse
import subprocess

def run_command(command):
    """명령어를 실행하고 결과를 출력합니다."""
    print(f"실행: {command}")
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"오류 발생: {result.stderr}")
        return False
    print(result.stdout)
    return True

def get_available_languages():
    """translations 폴더 내의 모든 언어 코드를 반환합니다."""
    if not os.path.exists("translations"):
        return []

    return [lang for lang in os.listdir("translations")
            if os.path.isdir(os.path.join("translations", lang))]

def main():
    parser = argparse.ArgumentParser(description="Flask-Babel 다국어 관리 도구")
    parser.add_argument("--extract", action="store_true", help="메시지 추출")
    parser.add_argument("--init", help="새 언어 초기화 (언어 코드 필요, 예: 'ko,en,ja')")
    parser.add_argument("--update", action="store_true", help="모든 언어 파일 업데이트")
    parser.add_argument("--compile", action="store_true", help="모든 언어 파일 컴파일")
    parser.add_argument("--all", action="store_true", help="추출, 업데이트, 컴파일 모두 실행")

    args = parser.parse_args()

    if not (args.extract or args.init or args.update or args.compile or args.all):
        parser.print_help()
        return

    # POT 파일 추출
    if args.extract or args.all:
        print("메시지 추출 중...")
        if not run_command("pybabel extract -F babel.cfg -o messages.pot ."):
            return

    # 새 언어 초기화
    if args.init:
        languages = [lang.strip() for lang in args.init.split(",")]
        for lang in languages:
            if lang:
                print(f"{lang} 언어 초기화 중...")
                run_command(f"pybabel init -i messages.pot -d translations -l {lang}")

    # 기존 언어 파일 업데이트
    if args.update or args.all:
        available_langs = get_available_languages()
        if available_langs:
            print(f"언어 파일 업데이트 중: {', '.join(available_langs)}...")
            run_command("pybabel update -i messages.pot -d translations")
        else:
            print("업데이트할 언어 파일이 없습니다.")

    # 모든 언어 파일 컴파일
#    if args.compile or args.all:
    if args.compile:
        print("언어 파일 컴파일 중...")
        run_command("pybabel compile -d translations")

if __name__ == "__main__":
    main()
