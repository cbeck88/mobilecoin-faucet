mobilecoin-faucet
=================

A faucet is a website where you can paste a mobilecoin address and it will send
you a small amount of MOB.

Technical overview
------------------

* This is a flask app which talks to full service to send transactions
* The mobilecoin addresses are the b58 format
* The amount of mob sent is a configurable parameter, but probably slightly larger
than the fee, like .01 MOB
* We will add options for abuse prevention features


Preparing the environment
-------------------------
```
python3 -m venv env
. ./env/bin/activate
pip install -r requirements.txt
FLASK_APP=faucet_server flask init-db
````

Running the server
------------------

```
FLASK_APP=faucet_server flask run
```
