import os


token = os.getenv('BOT_TOKEN') # your bot's token
client_id = os.getenv('CLIENT_ID')# your bot's client id
owner_id = os.getenv('OWNER_ID')
if not token and not client_id and not owner_id:
    raise SystemError('You must set the following environment variables: BOT_TOKEN, CLIENT_ID, OWNER_ID')
discordbotlist_key = '' # discordbotlist.com api key
emojis = {
    'name': '<:name:739452243262046240>',
    'limit': '<:limit:739452243895517254>',
    'category': '<:category:739452243949912176>',
    'position': '<:position:739452243861962962>',
    'help': '<:help:739471623702315029>',
    'exit': '<:exit:739452244298301501>'
}