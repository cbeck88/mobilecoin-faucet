import sys
import os
import click
import math
import mobilecoin
from mobilecoin.client import WalletAPIError
import requests
import sqlite3

from flask import (
    Flask,
    request,
    redirect,
    render_template,
    flash,
    url_for,
    g,
    current_app,
)

DATABASE = "faucet.db"
FULL_SERVICE_URL = os.environ.get("FULL_SERVICE", "http://localhost:9090/wallet")
full_service_client = mobilecoin.Client(FULL_SERVICE_URL)

app = Flask(__name__)
app.secret_key = "very extremely secret guys"

PAYMENT_AMOUNT = 0.01
# Set this to None to disable captchas
HCAPTCHA_SITE_KEY = None
# HCAPTCHA_SITE_KEY = "d1986f6b-0e08-4980-a6dd-00f36484f80c"
HCAPTCHA_SECRET = "0xa43F7aA369D873B361CE50EDf536ceD114EE274b"

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            DATABASE,
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row

    return g.db


def close_db(e=None):
    db = g.pop('db', None)

    if db is not None:
        db.close()

def init_db():
    db = get_db()

    with current_app.open_resource('schema.sql') as f:
        db.executescript(f.read().decode('utf8'))


@app.cli.command("init-db")
def init_db_command():
    """Clear the existing data and create new tables."""
    init_db()
    click.echo('Initialized the database.')

app.teardown_appcontext(close_db)
app.cli.add_command(init_db_command)

@app.route("/", methods=["GET", "POST"])
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

        # Check rate limit for IP
        db = get_db()

        account_id = get_account_id()
        address = request.form['address']
        try:
            r = full_service_client.build_and_submit_transaction(account_id, PAYMENT_AMOUNT, address)
        except WalletAPIError as e:
            if 'InvalidPublicAddress' in e.response['error']['data']['server_error']:
                flash("It didn't work. You give me a funny address or somethin?")
            else:
                print(e)
                flash("It didn't work, and I dunno why.")
            return redirect(url_for("faucet"))

        print(r)

        if r["failure_code"]:
            print(r)
            flash("It didn't work, and I really don't know why")
            return redirect(url_for("faucet"))

        # Happy path
        # log in db
        db.cursor().execute("INSERT INTO activity VALUES (?,?,?)", request.remote_addr, address, int(r["value_pmob"]))         
        flash("Okay, I paid you {} MOB at {}. You happy now?".format(PAYMENT_AMOUNT, address))
        return redirect(url_for("faucet"))
    else:
        return render_template('faucet.html', hcaptcha_site_key=HCAPTCHA_SITE_KEY)


def get_account_id():
    accounts = full_service_client.get_all_accounts()
    for account in accounts.values():
        if account['name'] == 'faucet':
            return account['account_id']
    else:
        raise Exception("No accounts returned from full-service")


@app.cli.command("create-account")
def create_account():
    accounts = full_service_client.get_all_accounts()
    if accounts:
        raise Exception("This full-service instance already has accounts set up")

    account = full_service_client.create_account("faucet")
    print(account)


@app.cli.command("balance")
def balance():
    account_id = get_account_id()
    response = full_service_client.get_balance_for_account(account_id)
    print(response)
    print("MOB:", mobilecoin.pmob2mob(response["unspent_pmob"]))


@app.cli.command("pubaddr")
def pubaddr():
    account_id = get_account_id()
    account = full_service_client.get_account(account_id)
    print(account["main_address"])


@app.cli.command("txos")
def txos():
    account_id = get_account_id()
    for txo in full_service_client.get_all_txos_for_account(account_id).values():
        print("{}: {} MOB (spent @ {})".format(txo["txo_id_hex"], mobilecoin.pmob2mob(txo["value_pmob"]), txo["spent_block_index"]))


@app.cli.command("split-txos")
@click.option("--value", default=0.1, help="Value in MOB, excluding fees, to send", type=float)
@click.option("--count", help="The amount of UTXOs we want to end up with", type=int)
def split_txos(value, count):
    account_id = get_account_id()
    our_pub_addr = full_service_client.get_account(account_id)["main_address"]

    # Figure out the amount we want to send
    min_fee = int(full_service_client.get_network_status()["fee_pmob"])
    utxo_value_pmob = min_fee + mobilecoin.mob2pmob(value)
    print("Output value:", utxo_value_pmob)

    # Number of transactions we need to submit to the network.
    # We can have up to 16 outputs, but for simplicity we reserve one for change.
    num_txs_needed = math.ceil(count / 15)
    print("Number of txs needed:", num_txs_needed)

    # Total unspent amount we need
    total_amount_needed = (utxo_value_pmob * count) + (min_fee * num_txs_needed)
    print("Total amount needed in MOB:", mobilecoin.pmob2mob(total_amount_needed))

    outputs_generated = 0
    i = 0
    while outputs_generated < count:
        num_outputs = min(15, count - outputs_generated)

        amount_needed = (utxo_value_pmob * num_outputs) + min_fee

        # See if we can find an unspent txo that is big enough for our purpose
        candidate_txos = full_service_client.get_all_txos_for_account(account_id).values()
        suitable_txos = [txo for txo in candidate_txos if not txo["spent_block_index"] and int(txo["value_pmob"]) >= amount_needed]
        if not suitable_txos:
            raise Exception("Failed to find a suitable txo to split")

        input_txo = suitable_txos[0]

        print("Iteration {}: generating {} outputs from txo {} ({} MOB)".format(
            i,
            num_outputs,
            input_txo["txo_id_hex"],
            mobilecoin.pmob2mob(input_txo["value_pmob"]),
        ))

        outputs = [
            (our_pub_addr, str(utxo_value_pmob))
            for _ in range(num_outputs)
        ]

        r = full_service_client._req({
            "method": "build_and_submit_transaction",
            "params": {
                "account_id": account_id,
                "addresses_and_values": outputs,
                "input_txo_ids": [input_txo["txo_id_hex"]],
            }
        })

        transaction_log_id = r["transaction_log"]["transaction_log_id"]
        print("    TX {} submitted @ block {}".format(transaction_log_id, r["transaction_log"]["submitted_block_index"]))
        print("    ", end="")

        while True:
            response = full_service_client._req({
                "method": "get_transaction_log",
                "params": {
                    "transaction_log_id": transaction_log_id,
                }
            })
            status = response["transaction_log"]["status"]
            if status == "tx_status_succeeded":
                break
            elif status == "tx_status_pending":
                sys.stdout.write(".")
                sys.stdout.flush()
            else:
                raise Exception("unaccepted tx status: {}", format(response))

        print("Succeeded :)")

        outputs_generated += num_outputs
        i += 1
