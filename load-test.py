import time, requests, re
from threading import Thread, get_ident

DEST_ADDR = '65G5DuLxeRPp8ben1Tf7vdcj26gWrH4J6NXFDwZkZxAQYy2AVdnRkoXHuFDeSdBSGc5biNkhQbX6VfurVX8mE7AUXziB8oXNMMYHfRWwogy'
CONCURRENCY = 5

success = 0
fail = 0

def worker():
    global success, fail
    n_reqs = 0
    while True:
        print('Submitting #{} @ {}'.format(n_reqs, get_ident()))
        n_reqs += 1
        resp = requests.post('http://localhost:5000', data={"address": DEST_ADDR})
        if 'Okay, I paid you' in resp.text:
            success += 1
        else:
            fail += 1

            try:
                err_msg = re.findall(r'<li>(.*)</li>', resp.text)[0]
            except:
                err_msg = resp.text

            print('Failed', err_msg)

        print("Successes: {}       Failures: {}".format(success, fail))


for _ in range(CONCURRENCY):
    w = Thread(target=worker)
    w.start()

while True:
    time.sleep(1000)
