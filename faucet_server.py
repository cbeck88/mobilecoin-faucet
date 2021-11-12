import sys
import os
import click
import math
import mobilecoin
from mobilecoin.client import WalletAPIError
import requests
import sqlite3
import threading

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

app = Flask(__name__, static_folder='static')
app.secret_key = "very extremely secret guys"

PAYMENT_AMOUNT = 0.01
# Set this to None to disable captchas
#HCAPTCHA_SITE_KEY = None
HCAPTCHA_SITE_KEY = "d1986f6b-0e08-4980-a6dd-00f36484f80c"
HCAPTCHA_SECRET = "0xa43F7aA369D873B361CE50EDf536ceD114EE274b"
# Set this to None to disable rate limiting
COOLDOWN_PERIOD_SECONDS = 30
# This is how many times you can use the faucet per cooldown period
COOLDOWN_MAX_PAYMENTS = 2

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

TXO_LOCK = threading.Lock()
PICKED_TXO_IDS = []
def get_spendable_txo():
    account_id = get_account_id()
    min_fee = int(full_service_client.get_network_status()["fee_pmob"])
    min_amount = mobilecoin.mob2pmob(PAYMENT_AMOUNT) + min_fee

    candidate_txos = full_service_client.get_all_txos_for_account(account_id).values()

    TXO_LOCK.acquire()
    try:
        suitable_txos = [
            txo for txo in candidate_txos
            if (not txo["spent_block_index"]
                and int(txo["value_pmob"]) >= min_amount
                and txo["txo_id_hex"] not in PICKED_TXO_IDS
                and txo["account_status_map"][account_id]["txo_status"] == "txo_status_unspent")
        ]

        if not suitable_txos:
            raise Exception("Oops, it turns out I'm broke. Better luck next time.")

        txo = suitable_txos[0]
        PICKED_TXO_IDS.append(txo["txo_id_hex"])

        return txo

    finally:
        TXO_LOCK.release()


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

        address = request.form['address'].strip()

        # Check rate limit for IP
        db = get_db()

        if COOLDOWN_PERIOD_SECONDS:
            try:
                cursor = db.cursor()
                cursor.execute("SELECT * FROM activity WHERE ip_address = ? AND (CAST(strftime('%s', CURRENT_TIMESTAMP) as integer) - CAST(strftime('%s', created) as integer)) < ?;", (request.remote_addr, COOLDOWN_PERIOD_SECONDS))
                ip_matches = cursor.fetchall()
                print(ip_matches)
                if len(ip_matches) >= COOLDOWN_MAX_PAYMENTS:
                    flash("Try again later, kid")
                    return redirect(url_for("faucet"))

                cursor.execute("SELECT * FROM activity WHERE mob_address = ? AND (CAST(strftime('%s', CURRENT_TIMESTAMP) as integer) - CAST(strftime('%s', created) as integer)) < ?;", (address, COOLDOWN_PERIOD_SECONDS))
                addr_matches = cursor.fetchall()
                print(addr_matches)
                if len(addr_matches) >= COOLDOWN_MAX_PAYMENTS:
                    flash("Whoa, take it easy there, kid. Maybe try again later, huh?")
                    return redirect(url_for("faucet"))
            except Exception as e:
                print(e)
                flash("Hmm I'm forgetting something... what was that?")
                return redirect(url_for("faucet"))

        print("attempting to send payment")

        # Try to send the payment
        if send_payment(address, db) == 1:
            flash("Okay, I paid you {} MOB. You happy now, punk?".format(PAYMENT_AMOUNT))
        return redirect(url_for("faucet"))
    else:
        return render_template('faucet.html', hcaptcha_site_key=HCAPTCHA_SITE_KEY, prompt="Hey kid, you want some magic internet money? What's your MobileCoin address?", form_action="/", mob_amount = PAYMENT_AMOUNT, cooldown_seconds = COOLDOWN_PERIOD_SECONDS, mob_address = get_pubaddr())

@app.route("/batch", methods=["GET", "POST"])
def batch():
    if request.method == 'POST':
        db = get_db()

        addresses = request.form['address'].split()
        successes = []
        failures = []
        print(addresses)
        for address in addresses:
            if send_payment(address, db) == 1:
                successes.append(address)
            else:
                failures.append(address)

        print("Successes: {}, Failures: {}".format(len(successes), len(failures)))

        if not failures:
            flash("Paid all {} addresses successfully".format(len(successes)))
        elif not successes:
            flash("All payments failed.")
        else:
            flash("Successfully paid {}".format(successes))
            flash("Failed to pay {}".format(failures))
        return redirect(url_for("batch"))
    else:
        return render_template('faucet.html', hcaptcha_site_key=HCAPTCHA_SITE_KEY, prompt="Batch pay any number of mobilecoin addresses", form_action="/batch", mob_amount = PAYMENT_AMOUNT, cooldown_seconds = COOLDOWN_PERIOD_SECONDS, mob_address = get_pubaddr())

# Try to send a payment to an address, given a db connection
#
# Returns 1 if payment succeeded, 0 if not.
# Flashes error messages but not success messages to allow the batch mode to have a different success message
def send_payment(address, db):
    account_id = get_account_id()
    try:
        spendable_txo = get_spendable_txo()

        r = full_service_client._req({
            "method": "build_and_submit_transaction",
            "params": {
                "account_id": account_id,
                "addresses_and_values": [(address, str(mobilecoin.mob2pmob(PAYMENT_AMOUNT)))],
                "input_txo_ids": [spendable_txo["txo_id_hex"]],
            }
        })
    except WalletAPIError as e:
        if 'InvalidPublicAddress' in e.response['error']['data']['server_error']:
            flash("It didn't work. You give me a funny address or somethin?")
        else:
            print(e)
            flash("It didn't work, and I dunno why.")

    except Exception as e:
        print(e)
        flash("{}".format(e))

    else:
        # Happy path
        # log in db
        try:
            value_pmob = int(r["transaction_log"]["value_pmob"])
            print("value pmob = {}".format(value_pmob))
            cursor = db.cursor()
            cursor.execute("INSERT INTO activity (ip_address, mob_address, amount_pmob_sent) VALUES (?,?,?)", (request.remote_addr, address, value_pmob))
            db.commit()
        except Exception as e:
            print("Database error: {}".format(e))
            print(r)
        return 1
    return 0

def get_account_id():
    accounts = full_service_client.get_all_accounts()
    for account in accounts.values():
        if account['name'] == 'faucet':
            return account['account_id']
    else:
        raise Exception("No accounts returned from full-service")

def get_pubaddr():
    account_id = get_account_id()
    account = full_service_client.get_account(account_id)
    return account["main_address"]

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
    print(get_pubaddr())

@app.cli.command("txos")
def txos():
    account_id = get_account_id()
    for txo in full_service_client.get_all_txos_for_account(account_id).values():
        print("{}: {} MOB (spent @ {})".format(txo["txo_id_hex"], mobilecoin.pmob2mob(txo["value_pmob"]), txo["spent_block_index"]))


@app.cli.command("split-txos")
@click.option("--value", default=PAYMENT_AMOUNT, help="Value in MOB, excluding fees, to send", type=float)
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
        suitable_txos = [
            txo for txo in candidate_txos if (
                not txo["spent_block_index"]
                and int(txo["value_pmob"]) >= amount_needed
                and txo["account_status_map"][account_id]["txo_status"] == "txo_status_unspent"

            )]
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
