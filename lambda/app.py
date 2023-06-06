from email.message import Message
from botocore.exceptions import ClientError
from datetime import datetime
import boto3
import email
import os
import base64
import pandas as pd


def lambda_handler(event, context):
    from_address = event["envelope"]["mailFrom"]["address"]
    subject = event["subject"]
    flow_direction = event["flowDirection"]
    message_id = event["messageId"]
    print(
        f"Received email with message ID {message_id}, flowDirection {flow_direction}, from {from_address} with Subject {subject}"
    )

    try:
        s3 = boto3.client("s3")
        workmail = boto3.client("workmailmessageflow")

        raw_msg = workmail.get_raw_message_content(messageId=message_id)
        parsed_msg: Message = email.message_from_bytes(raw_msg["messageContent"].read())
        # print(parsed_msg)

        destination_bucket = os.getenv("DESTINATION_BUCKET")
        destination_file = os.getenv("DESTINATION_FILE")
        origin_file = os.getenv("ORIGIN_FILE")
        sheet_name = "Sheet1"
        current_date = datetime.now()
        year = current_date.year
        date_string = current_date.strftime("%Y/%m/%d")

        if not destination_bucket:
            print("DESTINATION_BUCKET not set in environment")
            return

        # Match Excel file type
        attachment = get_attachment(
            parsed_msg,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        if attachment is None:
            print("Attachment is not found")
        else:
            excel_content = attachment.get_payload(decode=True)
            with open(f"/tmp/{origin_file}", "wb") as excel:
                excel.write(excel_content)

            # Save the original file to S3
            s3.put_object(
                Body=excel_content,
                Bucket=destination_bucket,
                Key=f"{date_string}/{origin_file}",
            )

            df = pd.DataFrame(
                pd.read_excel(
                    f"/tmp/{origin_file}", engine="openpyxl", sheet_name=sheet_name
                )
            )
            print(df)

            csv_content = bytes(
                df.to_csv(lineterminator="\r\n", index=False), encoding="utf-8"
            )

            # Save the CSV file to S3
            s3.put_object(
                Body=csv_content,
                Bucket=destination_bucket,
                Key=f"{date_string}/{destination_file}",
            )

        print("Done")

    except ClientError as e:
        if e.response["Error"]["Code"] == "MessageFrozen":
            # Redirect emails are not eligible for update, handle it gracefully.
            print(
                f"Message {message_id} is not eligible for update. This is usually the case for a redirected email"
            )
        else:
            # Send some context about this error to Lambda Logs
            print(e)
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                print(
                    f"Message {message_id} does not exist. Messages in transit are no longer accessible after 1 day"
                )
            elif e.response["Error"]["Code"] == "InvalidContentLocation":
                print(
                    "WorkMail could not access the updated email content. See https://docs.aws.amazon.com/workmail/latest/adminguide/update-with-lambda.html"
                )
            raise (e)

    # Return value is ignored when Lambda is configured asynchronously at Amazon WorkMail
    # For more information, see https://docs.aws.amazon.com/workmail/latest/adminguide/lambda.html
    return {
        "actions": [
            {
                "allRecipients": True,  # For all recipients
                "action": {"type": "DEFAULT"},  # let the email be sent normally
            }
        ]
    }


# Modified from:
# https://medium.com/caspertechteam/processing-email-attachments-with-aws-a35a1411a0c4
def get_attachment(msg, content_type):
    attachment = None

    msg_content_type = msg.get_content_type()
    # print(msg_content_type)

    if msg_content_type == content_type:
        return msg

    elif msg_content_type.startswith("multipart/"):
        for part in msg.get_payload():
            attachment = get_attachment(part, content_type)

    return attachment


def is_base64(sb):
    try:
        if isinstance(sb, str):
            # If there's any unicode here, an exception will be thrown and the function will return false
            sb_bytes = bytes(sb, "ascii")
        elif isinstance(sb, bytes):
            sb_bytes = sb
        else:
            raise ValueError("Argument must be string or bytes")
        return base64.b64encode(base64.b64decode(sb_bytes)) == sb_bytes
    except Exception:
        return False
