"""CLI: GigaChat+SQLite by default, --offline to test without API.
Run: python demo.py | python demo.py --offline | python demo.py --memory
"""
import argparse
from assistant_ai.factory import make_application


def main():
    parser=argparse.ArgumentParser(description='Голосовой/текстовый помощник: консоль')
    parser.add_argument('--offline',action='store_true',help='диагностика без GigaChat (упрощённые правила)')
    parser.add_argument('--memory',action='store_true',help='временная БД для тестов')
    args=parser.parse_args()
    try:app,client=make_application(offline=args.offline,memory=args.memory)
    except Exception as exc:raise SystemExit(str(exc))
    try:
        print('Введите «выход» для завершения. Хранилище: '+('временное' if args.memory else 'SQLite (сохранение между запусками)'))
        if args.offline:print('Режим без GigaChat: ограниченное распознавание, только для диагностики.')
        while True:
            try:text=input('Вы: ').strip()
            except (EOFError,KeyboardInterrupt):break
            if text.lower() in ('выход','exit'):break
            if text:print('Ассистент:',app.handle_message(text)['display_text'])
    finally:
        if client is not None:client.close()
        if hasattr(app.crud,'close'):app.crud.close()

if __name__=='__main__':main()
