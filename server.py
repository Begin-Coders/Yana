import logging
import warnings

from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import undetected_chromedriver as uc

import telegram
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.helpers import escape_markdown
from telegram import ReplyKeyboardMarkup, KeyboardButton

import nest_asyncio

from PIL import Image
from stability_sdk import client
import stability_sdk.interfaces.gooseai.generation.generation_pb2 as generation

import openai

from dotenv import load_dotenv
import os
import time
import random

from settings import *
from response import *

# Env Setup
load_dotenv()


# Asyncio Setup
nest_asyncio.apply()


# OpenAI Setup
openai.api_key = os.environ["OPENAI_API_KEY"]


# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# DreamStudio Handler
def formate_prompt(prompt: str) -> tuple:
    prompt_string = prompt.replace("\\", "")
    prompts = prompt_string.split("|")

    generation_prompts = []
    seed = 0
    for prompt in prompts:
        prompt_parts = prompt.split(":")
        text = prompt_parts[0].strip()
        weight = 1.0
        if len(prompt_parts) > 1:
            try:
                weight = float(prompt_parts[1].strip())
            except ValueError:
                weight = 1.0

        if text == "seed":
            seed = int(weight)
            continue

        generation_prompt = generation.Prompt(
            text=text, parameters=generation.PromptParameters(weight=weight)
        )
        generation_prompts.append(generation_prompt)

    return generation_prompts, seed


async def respond_with_image(update: Update, response: str) -> None:
    prompt = response.split("\[prompt:")[1].split("\]")[0]
    await update.message.reply_text(
        f"Generating image with prompt `{prompt.strip()}`",
        parse_mode=telegram.constants.ParseMode.MARKDOWN_V2,
    )
    await application.bot.send_chat_action(update.effective_chat.id, "typing")
    ds_api = client.StabilityInference(
        key=os.getenv("STABILITY_API_KEY"),
        verbose=True,
        engine="stable-diffusion-768-v2-1",
    )
    split_prompt, seed = formate_prompt(prompt)
    responses = ds_api.generate(
        prompt=split_prompt,
        seed=seed,
        steps=50,
        cfg_scale=7.0,
        width=768,
        height=768,
        sampler=generation.SAMPLER_K_DPMPP_2S_ANCESTRAL,
        guidance_preset=generation.GUIDANCE_PRESET_FAST_GREEN,
    )
    for resp in responses:
        for artifact in resp.artifacts:
            if artifact.finish_reason == generation.FILTER:
                warnings.warn(
                    "Your request activated the API's safety filters and could not be processed."
                    "Please modify the prompt and try again."
                )
            if artifact.type == generation.ARTIFACT_IMAGE:
                image, seed = artifact.binary, artifact.seed
    send_message_to_chatgpt(
        f"""
    Your image generated a seed of `{seed}`. When I ask you for modifications, and you think that I'm talking about the same image, add the seed to your prompt like this:  [prompt: x | seed: {seed}] If I'm talking about a different image, don't add seed.
        """
    )
    await update.message.reply_photo(
        photo=image,
        caption=f"chatGPT generated prompt: {prompt}",
        parse_mode=telegram.constants.ParseMode.MARKDOWN_V2,
    )


# ChatGPT Handler
def send_message_to_chatgpt(message: str) -> None:
    if API:
        openai.Completion.create(
            engine="text-davinci-003",
            prompt=message,
            n=1,
            temperature=1,
            frequency_penalty=1,
        )
    else:
        textbox = driver.find_element(By.CSS_SELECTOR, "textarea")
        textbox.click()
        textbox.send_keys(message)
        textbox.send_keys(Keys.RETURN)


async def checking_for_message_to_finish(update: Update):
    submit_button = driver.find_elements(By.CSS_SELECTOR, "textarea+button")[0]
    loading = submit_button.find_elements(By.CSS_SELECTOR, ".text-2xl")
    await application.bot.send_chat_action(update.effective_chat.id, "typing")
    start_time = time.time()
    while len(loading) > 0:
        if time.time() - start_time > 90:
            break
        time.sleep(0.5)
        loading = submit_button.find_elements(By.CSS_SELECTOR, ".text-2xl")
        await application.bot.send_chat_action(update.effective_chat.id, "typing")


def get_message_from_chatgpt() -> str:
    page_elements = driver.find_elements(
        By.CSS_SELECTOR,
        "div.markdown.prose.w-full.break-words.dark\\:prose-invert.light",
    )
    # logger.info(page_elements)
    last_element = page_elements[-1]
    prose = last_element
    try:
        code_blocks = prose.find_elements(By.CSS_SELECTOR, "pre")
    except Exception as e:
        response = "Server probably disconnected, try running /reload"
        return response

    if len(code_blocks) > 0:
        response = ""
        for child in prose.find_elements(By.CSS_SELECTOR, "p,pre"):
            # print(child.get_property('tagName'))
            if str(child.get_property("tagName")) == "PRE":
                code_container = child.find_element(By.CSS_SELECTOR, "code")
                response += (
                    f"\n```\n{escape_markdown(code_container.text, version=2)}\n```"
                )
            else:
                text = child.get_attribute("innerHTML")
                response += escape_markdown(text, version=2)
        response = response.replace("<code\>", "`")
        response = response.replace("</code\>", "`")
    else:
        response = escape_markdown(prose.text, version=2)
    return response


# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"User {update.effective_user.first_name} started the conversation.")

    help_button = KeyboardButton("Help")
    imagine_button = KeyboardButton("Imagine")
    keyboard = [[imagine_button, help_button]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    user = update.effective_user
    await update.message.reply_html(
        WELCOME_TEXT(user),
        reply_markup=reply_markup
    )


async def imagine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"User {update.effective_user.first_name} drew {update.message.text}.")

    send_message_to_chatgpt(MAKE_DRAW(update))
    await checking_for_message_to_finish(update)
    response = get_message_from_chatgpt()
    if "\[prompt:" in response:
        await application.bot.send_chat_action(
            update.effective_chat.id, telegram.constants.ChatAction.UPLOAD_PHOTO
        )
        await respond_with_image(update, response)


async def reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"User {update.effective_user.first_name} reloaded the browser.")

    driver.get(driver.current_url)
    await update.message.reply_text("Reloaded the browser!!")


async def help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"User {update.effective_user.first_name} asked help!")

    await update.message.reply_text("<text> - Send any text, our assistent will reply you.\n/imagine <text> - Will generate a image\n/reload - Reload the browser")


async def message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"User {update.effective_user.first_name} sent {update.message.text}.")

    if update.message.text == "Help":
        context.bot.execute_command("help")

    if API:
        response = openai.Completion.create(
            engine="text-davinci-003",
            temperature=1,
            prompt=f"{CHANGE_YOUR_SELF(update)}\n{update.message.text}",
            n=1,
            frequency_penalty=1,
            stop=None,
            max_tokens=1024,
        )
        # print(response)
        if "\[prompt:" in response:
            await respond_with_image(update, response)
        else:
            await update.message.reply_text(response["choices"][0]["text"].strip())

    else:
        send_message_to_chatgpt(update.message.text)
        await checking_for_message_to_finish(update)
        response = get_message_from_chatgpt()
        if "\[prompt:" in response:
            await respond_with_image(update, response)
        else:
            await update.message.reply_text(
                response, parse_mode=telegram.constants.ParseMode.MARKDOWN_V2
            )


# Telegram Elements
def telegram_elements(application: ApplicationBuilder) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("imagine", imagine))
    application.add_handler(CommandHandler("reload", reload))
    application.add_handler(CommandHandler("help", help))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))


# Browser Startup
def browser_startup() -> None:
    if API:
        pass

    else:
        for _ in range(random.randrange(3, len(WEBSITES))):
            driver.get(random.choice(WEBSITES))
        driver.get("https://chat.openai.com/")

        try:
            checkbox = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//*[@id='cf-stage']/div[6]/label/span")
                )
            )
            logger.info("Checking for Human, So cheating it!!")
            checkbox.click()
        except:
            pass

        try:
            button = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//*[@id='challenge-stage']/div/input")
                )
            )
            logger.info("Checking for Human, So cheating it!!")
            button.click()
        except:
            pass

        try:
            driver.find_element(By.LINK_TEXT, "ChatGPT is at capacity right now")
            raise ConnectionRefusedError("ChatGPT Server Busy Try Again Some Time")
        except:
            pass

        try:
            textarea = driver.find_element(By.TAG_NAME, "textarea")
        except:
            logger.info("You are not logged in!!, So Trying to logging in!!")

            if LOGIN_WITH_GOOGLE:
                driver.find_element_by_xpath(
                    "//button[contains(text(), 'Log in')]"
                ).click()

                if driver.find_element_by_xpath("//button[contains(text(), 'Next')]"):
                    raise ConnectionRefusedError("No Google Account Found")

                profile_images = driver.find_elements_by_tag_name("img")
                profile_images[GOOGLE_ACCOUNT_TILE].click()

            else:
                if OPEN_AI_EMAIL == "" and OPEN_AI_PASSWORD == "":
                    raise NameError("Mail and Password not found!!")

                # with open("new.html", "w") as f:
                #     f.write(driver.page_source)

                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//*[@id='__next']/div[1]/div/div[4]/button[1]")
                    )
                ).click()

                username = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, '//*[@id="username"]'))
                )
                username.send_keys(OPEN_AI_EMAIL)
                username.send_keys(Keys.RETURN)

                password = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, '//*[@id="password"]'))
                )
                password.send_keys(OPEN_AI_PASSWORD)
                password.send_keys(Keys.RETURN)

                try:
                    next_button = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located(
                            (
                                By.XPATH,
                                "//*[@id='headlessui-dialog-panel-:r1:']/div[2]/div[4]/button",
                            )
                        )
                    )
                    next_button.click()
                    next_button = WebDriverWait(driver, 1).until(
                        EC.presence_of_element_located(
                            (
                                By.XPATH,
                                "//*[@id='headlessui-dialog-panel-:r1:']/div[2]/div[4]/button",
                            )
                        )
                    )
                    next_button.click()
                    next_button = WebDriverWait(driver, 1).until(
                        EC.presence_of_element_located(
                            (
                                By.XPATH,
                                "//*[@id='headlessui-dialog-panel-:r1:']/div[2]/div[4]/button",
                            )
                        )
                    )
                    next_button.click()
                except:
                    pass
        time.sleep(3)
        # send_message_to_chatgpt(CHANGE_YOUR_SELF(Update))
    logger.info("Logged in!!")


if __name__ == "__main__":
    # Importing ENV's
    TELEGRAM_API = os.environ["TELEGRAM_API_KEY"]
    OPEN_AI_EMAIL = os.environ["OPEN_AI_EMAIL"]
    OPEN_AI_PASSWORD = os.environ["OPEN_AI_PASSWORD"]

    # Selenium Setup
    # options = webdriver.ChromeOptions()
    # options.add_argument("--no-sandbox")
    # options.add_argument("--enable-javascript")
    # options.add_argument("--disable-dev-shm-usage")
    # options.add_argument("--disable-blink-features=AutomationControlled")
    # driver = webdriver.Remote(command_executor='http://localhost:4444', options=options)

    options = uc.ChromeOptions()
    options.user_data_dir = "./temp/profile"
    options.arguments.extend(["--no-sandbox", "--disable-setuid-sandbox"])
    if HEADLESS_MODE:
        options.headless = HEADLESS_MODE
        options.add_argument('--headless')
    driver = uc.Chrome(options)

    # Bot Setup
    application = ApplicationBuilder().token(TELEGRAM_API).build()
    telegram_elements(application)

    browser_startup()

    application.run_polling()
