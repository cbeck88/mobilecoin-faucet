from flask import (
    Flask,
    request,
    redirect,
    render_template,
    flash,
    url_for,
)

import requests

app = Flask(__name__)
app.secret_key = 'very extremely secret guys'

PAYMENT_AMOUNT = 0.01
# Set this to None to disable captchas
#HCAPTCHA_SITE_KEY = None
HCAPTCHA_SITE_KEY = "d1986f6b-0e08-4980-a6dd-00f36484f80c"
HCAPTCHA_SECRET = "0xa43F7aA369D873B361CE50EDf536ceD114EE274b"

@app.route("/", methods=['GET', 'POST'])
def faucet():
    if request.method == 'POST':
        if HCAPTCHA_SITE_KEY:
            # Verify captcha
            token = request.form['h-captcha-response']
            params = {
                "secret": HCAPTCHA_SECRET,
                "response": token,
                "sitekey": HCAPTCHA_SITE_KEY,
                "remoteip": request.remote_addr,
            }
            response = requests.post("https://hcaptcha.com/siteverify", params)
            if not response.json()['success']:
                flash('You must complete the CAPTCHA to receive a payment')
                return redirect(url_for("faucet"))

        address = request.form['address']
        # TODO: send the payment
        flash('Okay, I paid you {} MOB at {}. You happy now?'.format(PAYMENT_AMOUNT, address))
        return redirect(url_for("faucet"))
    else:
        return render_template('faucet.html', hcaptcha_site_key = HCAPTCHA_SITE_KEY)
