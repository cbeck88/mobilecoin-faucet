from flask import (
    Flask,
    request,
    redirect,
    render_template,
    flash,
    url_for,
)

app = Flask(__name__)
app.secret_key = 'very extremely secret guys'

PAYMENT_AMOUNT = 0.01

@app.route("/", methods=['GET', 'POST'])
def faucet():
    if request.method == 'POST':
        address = request.form['address']
        # TODO: send the payment
        flash('Okay, I paid you {} MOB at {}. You happy now?'.format(PAYMENT_AMOUNT, address))
        return redirect(url_for("faucet"))
    else:
        return render_template('faucet.html')
