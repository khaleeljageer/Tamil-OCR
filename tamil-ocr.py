from ocr_tamil.ocr import OCR

image_path = 'img_1.png'


def line_print(prediction):
    current_line = 1
    extracted_text = ""
    for text in prediction:
        pred_text = text[0]
        line_details = text[2][1]

        if line_details != current_line:
            extracted_text += "\n" + pred_text + " "
            current_line = line_details
        else:
            extracted_text += pred_text + " "
    return extracted_text


ocr = OCR(detect=True, details=2, batch_size=128)
text_list = ocr.predict([image_path])

print(line_print(text_list[0]))
