import json
import threading

from app.client import analysis, request
from app.server import make_server


def show(title, payload):
    print('== ' + title)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    with make_server() as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        print(json.dumps(request(base, '/health'), ensure_ascii=False))
        for case in request(base, '/cases')['cases']:
            response = request(base, '/preview', {'case': case})
            print(json.dumps({'case': case, 'preview': response}, ensure_ascii=False))
        # 新能力：流程符合性分析。输入口径为 case 与观察截止 cutoff，
        # 输出逐订单判定、商户聚合、共享事件与无法判定的分支说明。
        for case in request(base, '/cases')['cases']:
            result = analysis(base, case)
            show('conformance ' + case + ' (cutoff=%s)' % result['cutoff'], result)
        # 截尾演示：观察提前结束时，进行中的订单记为 open 而非偏离。
        early = analysis(base, 'standard', cutoff=2)
        show('conformance standard (cutoff=2, 截尾)', early)
        server.shutdown()
        worker.join(timeout=3)


if __name__ == '__main__':
    main()
