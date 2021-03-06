"""Uses downloaded files to create training and dev data.
"""

import json
import numpy as np
import os
import preprocessing.constants as constants
import re
import spacy
import time

from preprocessing.dataset_files_saver import *
from preprocessing.dataset_files_wrapper import *
from preprocessing.file_util import *
from preprocessing.raw_training_data import *
from preprocessing.spacy_util import create_tokenizer
from preprocessing.string_category import *
from preprocessing.vocab import get_vocab
from util.string_util import *

#파일을 읽어들이는 내장함수인 load_workbook을 불러옵니다.
#from openpyxl import load_workbook

_BOS = "bos"
_EOS = "eos"

_DEBUG_USE_ONLY_FIRST_ARTICLE = False

# Note: Some of the training/dev data seems to be inaccurate. This code
# tries to make sure that at least one of the "qa" options in the acceptable
# answers list is accurate and includes it in the data set.

class TextPosition:
    def __init__(self, start_idx, end_idx):
        self.start_idx = start_idx
        self.end_idx = end_idx

class PassageContext:
    '''Class used to save the tokenization positions in a given passage
       so that the original strings can be used for constructing answer
       spans rather than joining tokenized strings, which isn't 100% correct.
    '''
    def __init__(self, passage_str, word_id_to_text_positions,
        acceptable_gnd_truths):
        self.passage_str = passage_str
        self.word_id_to_text_positions = word_id_to_text_positions
        self.acceptable_gnd_truths = acceptable_gnd_truths

class DataParser():
    def __init__(self, data_dir, download_dir):
        self.data_dir = data_dir
        self.download_dir = download_dir
        self.value_idx = 0
        self.question_id = 0
        self.ner_categories = StringCategory()
        self.pos_categories = StringCategory()

    def _parse_data_from_tokens_list(self, tokens_list, tokens_ner_dict):
        """Input: A spaCy doc.

           Ouptut: (vocab_ids_list, vocab_ids_set, pos_list, ner_list)
        """
        vocab_ids_list = []
        vocab_ids_set = set()
        pos_list = []
        ner_list = []
        for zz in range(len(tokens_list)):
            token = tokens_list[zz]
            vocab_id = None
            token_pos = None
            token_ner = None
            if not isinstance(token, spacy.tokens.token.Token) and token == _BOS:
                vocab_id = self.vocab.BOS_ID
                token_pos = "bos"
                token_ner = "bos"
            elif not isinstance(token, spacy.tokens.token.Token) and token == _EOS:
                vocab_id = self.vocab.EOS_ID
                token_pos = "eos"
                token_ner = "eos"
            else:
                word = token.text
                vocab_id = self.vocab.get_id_for_word(word)
                token_pos = token.pos_
                token_ner = tokens_ner_dict[token.idx].label_ \
                    if token.idx in tokens_ner_dict else "none"
                vocab_ids_set.add(vocab_id)
            vocab_ids_list.append(vocab_id)
            pos_list.append(self.pos_categories.get_id_for_word(token_pos))
            ner_list.append(self.ner_categories.get_id_for_word(token_ner))
        return vocab_ids_list, vocab_ids_set, pos_list, ner_list

    def _maybe_add_samples(self, tok_context=None, tok_question=None, qa=None,
        ctx_offset_dict=None, ctx_end_offset_dict=None, list_contexts=None,
        list_word_in_question=None, list_questions=None,
        list_word_in_context=None, spans=None, num_values=None,
        question_ids=None,
        context_pos=None,
        question_pos=None, context_ner=None, question_ner=None,
        is_dev=None, ctx_ner_dict=None, qst_ner_dict=None,
        psg_ctx=None):
        first_answer = True
        for answer in qa["answers"]:
            answer_start = answer["answer_start"]
            text = answer["text"]
            answer_end = answer_start + len(text)
            tok_start = None
            tok_end = None
            exact_match = answer_start in ctx_offset_dict and answer_end in ctx_end_offset_dict
            if not exact_match:
                # Sometimes, the given answer isn't actually in the context.
                # If so, find the smallest surrounding text instead.
                for z in range(len(tok_context)):
                    tok = tok_context[z]
                    if not isinstance(tok, spacy.tokens.token.Token):
                        continue
                    st = tok.idx
                    end = st + len(tok.text)
                    if st <= answer_start and answer_start <= end:
                        tok_start = tok
                        if z == len(tok_context) - 2:
                            tok_end = tok
                    elif tok_start is not None:
                        tok_end = tok
                        if end >= answer_end:
                            break
            tok_start = tok_start if tok_start is not None else ctx_offset_dict[answer_start]
            tok_end = tok_end if tok_end is not None else ctx_end_offset_dict[answer_end]
            tok_start_idx, tok_end_idx = None, None
            for z in range(len(tok_context)):
                tok = tok_context[z]
                if not isinstance(tok, spacy.tokens.token.Token): # BOS, EOS
                    continue
                if tok == tok_start:
                    tok_start_idx = z
                if tok == tok_end:
                    tok_end_idx = z
                if tok_start_idx is not None and tok_end_idx is not None:
                    break
            assert(tok_start_idx is not None)
            assert(tok_end_idx is not None)
            # For dev, only keep one exmaple per question, and the set of all
            # acceptable answers. This reduces the required memory for storing
            # data.
            if is_dev and not first_answer:
                continue
            first_answer = False

            spans.append([tok_start_idx, tok_end_idx])
            question_ids.append(self.question_id)

            ctx_vocab_ids_list, ctx_vocab_ids_set, \
                ctx_pos_list, ctx_ner_list = \
                self._parse_data_from_tokens_list(tok_context, ctx_ner_dict)
            list_contexts.append(ctx_vocab_ids_list)
            context_pos.append(ctx_pos_list)
            context_ner.append(ctx_ner_list)

            qst_vocab_ids_list, qst_vocab_ids_set, \
                qst_pos_list, qst_ner_list = \
                self._parse_data_from_tokens_list(tok_question, qst_ner_dict)
            list_questions.append(qst_vocab_ids_list)
            question_pos.append(qst_pos_list)
            question_ner.append(qst_ner_list)

            word_in_question_list = [1 if word_id in qst_vocab_ids_set else 0 for word_id in ctx_vocab_ids_list]
            word_in_context_list = [1 if word_id in ctx_vocab_ids_set else 0 for word_id in qst_vocab_ids_list]
            list_word_in_question.append(word_in_question_list)
            list_word_in_context.append(word_in_context_list)
            print("Value", self.value_idx, "of", num_values, "percent done",
                  100 * float(self.value_idx) / float(num_values), end="\r")
            self.value_idx += 1

    def _maybe_add_samples_predict(self, tok_context=None, tok_question=None,
        ctx_offset_dict=None, ctx_end_offset_dict=None, list_contexts=None,
        list_word_in_question=None, list_questions=None,
        list_word_in_context=None, spans=None, num_values=None,
        question_ids=None,
        context_pos=None,
        question_pos=None, context_ner=None, question_ner=None,
        is_dev=None, ctx_ner_dict=None, qst_ner_dict=None,
        psg_ctx=None):
        first_answer = True

        # answer_start = answer["answer_start"]
        # text = answer["text"]
        # answer_end = answer_start + len(text)
        tok_start = None
        tok_end = None
        # exact_match = answer_start in ctx_offset_dict and answer_end in ctx_end_offset_dict
        # if not exact_match:
        #     # Sometimes, the given answer isn't actually in the context.
        #     # If so, find the smallest surrounding text instead.
        #     for z in range(len(tok_context)):
        #         tok = tok_context[z]
        #         if not isinstance(tok, spacy.tokens.token.Token):
        #             continue
        #         st = tok.idx
        #         end = st + len(tok.text)
        #         if st <= answer_start and answer_start <= end:
        #             tok_start = tok
        #             if z == len(tok_context) - 2:
        #                 tok_end = tok
        #         elif tok_start is not None:
        #             tok_end = tok
        #             if end >= answer_end:
        #                 break
        # tok_start = tok_start if tok_start is not None else ctx_offset_dict[answer_start]
        # tok_end = tok_end if tok_end is not None else ctx_end_offset_dict[answer_end]
        # tok_start_idx, tok_end_idx = None, None
        # for z in range(len(tok_context)):
        #     tok = tok_context[z]
        #     if not isinstance(tok, spacy.tokens.token.Token): # BOS, EOS
        #         continue
        #     if tok == tok_start:
        #         tok_start_idx = z
        #     if tok == tok_end:
        #         tok_end_idx = z
        #     if tok_start_idx is not None and tok_end_idx is not None:
        #         break
        # assert(tok_start_idx is not None)
        # assert(tok_end_idx is not None)
        # For dev, only keep one exmaple per question, and the set of all
        # acceptable answers. This reduces the required memory for storing
        # data.
        # if is_dev and not first_answer:
        #     continue
        # first_answer = False

        spans.append([0, 0])
        question_ids.append(self.question_id)

        ctx_vocab_ids_list, ctx_vocab_ids_set, \
            ctx_pos_list, ctx_ner_list = \
            self._parse_data_from_tokens_list(tok_context, ctx_ner_dict)
        list_contexts.append(ctx_vocab_ids_list)
        context_pos.append(ctx_pos_list)
        context_ner.append(ctx_ner_list)

        qst_vocab_ids_list, qst_vocab_ids_set, \
            qst_pos_list, qst_ner_list = \
            self._parse_data_from_tokens_list(tok_question, qst_ner_dict)
        list_questions.append(qst_vocab_ids_list)
        question_pos.append(qst_pos_list)
        question_ner.append(qst_ner_list)

        word_in_question_list = [1 if word_id in qst_vocab_ids_set else 0 for word_id in ctx_vocab_ids_list]
        word_in_context_list = [1 if word_id in ctx_vocab_ids_set else 0 for word_id in qst_vocab_ids_list]
        list_word_in_question.append(word_in_question_list)
        list_word_in_context.append(word_in_context_list)
        print("Value", self.value_idx, "of", num_values, "percent done",
              100 * float(self.value_idx) / float(num_values), end="\r")
        self.value_idx += 1

    def _get_num_data_values(self, dataset):
        num_values = 0
        for article in dataset:
            for paragraph in article["paragraphs"]:
                for qa in paragraph["qas"]:
                    num_values += 1
        return num_values

    def _get_ner_dict(self, doc):
        d = {}
        for e in doc.ents:
            d[e.start_char] = e
        return d

    def _create_train_data_internal(self, data_file, is_dev):
        """Returns (contexts, word_in_question, questions, word_in_context, spans)
            contexts: list of lists of integer word ids
            word_in_question: list of lists of booleans indicating whether each
                word in the context is present in the question
            questions: list of lists of integer word ids
            word_in_context: list of lists of booleans indicating whether each
                word in the question is present in the context
            spans: numpy array of shape (num_samples, 2)
            question_ids: a list of ints that indicates which question the
                given sample is part of. this has the same length as
                |contexts| and |questions|. multiple samples may come from
                the same question because there are potentially multiple valid
                answers for the same question
        """
        filename = os.path.join(self.download_dir, data_file)
        print("Reading data from file", filename)
        with open(filename, encoding="utf-8") as data_file:
            data = json.load(data_file)
            dataset = data["data"]
            num_values = self._get_num_data_values(dataset)
            spans = []
            list_contexts = []
            list_word_in_question = []
            list_questions = []
            list_word_in_context = []
            question_ids = []
            context_pos = []
            question_pos = []
            context_ner = []
            question_ner = []
            question_ids_to_squad_question_id = {}
            question_ids_to_passage_context = {}
            self.value_idx = 0
            for dataset_id in range(len(dataset)):
                if dataset_id > 0 and _DEBUG_USE_ONLY_FIRST_ARTICLE:
                    break
                article = dataset[dataset_id]
                for paragraph in article["paragraphs"]:
                    context = paragraph["context"]
                    tok_context = self.nlp(context)
                    tok_contexts_with_bos_and_eos = []
                    ctx_ner_dict = self._get_ner_dict(tok_context)
                    assert tok_context is not None
                    ctx_offset_dict = {}
                    ctx_end_offset_dict = {}
                    word_idx_to_text_position = {}

                    word_idx = 0
                    for sentence in tok_context.sents:
                        tok_contexts_with_bos_and_eos.append(_BOS)
                        word_idx_to_text_position[word_idx] = \
                            TextPosition(0, 0)
                        word_idx += 1
                        for token in sentence:
                            tok_contexts_with_bos_and_eos.append(token)
                            st = token.idx
                            end = token.idx + len(token.text)
                            ctx_offset_dict[st] = token
                            ctx_end_offset_dict[end] = token
                            word_idx_to_text_position[word_idx] = \
                                TextPosition(st, end)
                            word_idx += 1
                        tok_contexts_with_bos_and_eos.append(_EOS)
                        word_idx_to_text_position[word_idx] = \
                            TextPosition(0, 0)
                        word_idx += 1

#                    word_idx = 0
#                    tok_contexts_with_bos_and_eos.append(_BOS)
#                    word_idx_to_text_position[word_idx] = \
#                        TextPosition(0, 0)
#                    word_idx += 1
#                    for token in tok_context:
#                        tok_contexts_with_bos_and_eos.append(token)
#                        st = token.idx
#                        end = token.idx + len(token.text)
#                        ctx_offset_dict[st] = token
#                        ctx_end_offset_dict[end] = token
#                        word_idx_to_text_position[word_idx] = \
#                            TextPosition(st, end)
#                        word_idx += 1
#                    tok_contexts_with_bos_and_eos.append(_EOS)
#                    word_idx_to_text_position[word_idx] = \
#                        TextPosition(0, 0)

                    for qa in paragraph["qas"]:
                        self.question_id += 1
                        acceptable_gnd_truths = []
                        for answer in qa["answers"]:
                            acceptable_gnd_truths.append(answer["text"])
                        question_ids_to_passage_context[self.question_id] = \
                            PassageContext(context, word_idx_to_text_position,
                                acceptable_gnd_truths)
                        question = qa["question"]
                        squad_question_id = qa["id"]
                        assert squad_question_id is not None
                        question_ids_to_squad_question_id[self.question_id] = \
                            squad_question_id
                        tok_question = self.nlp(question)
                        tok_question_with_bos_and_eos = []

                        for sentence in tok_question.sents:
                            tok_question_with_bos_and_eos.append(_BOS)
                            for token in sentence:
                                tok_question_with_bos_and_eos.append(token)
                            tok_question_with_bos_and_eos.append(_EOS)

#                        tok_question_with_bos_and_eos.append(_BOS)
#                        for token in tok_question:
#                            tok_question_with_bos_and_eos.append(token)
#                        tok_question_with_bos_and_eos.append(_EOS)

                        qst_ner_dict = self._get_ner_dict(tok_question)
                        assert tok_question is not None
                        found_answer_in_context = False
                        found_answer_in_context = self._maybe_add_samples(
                            tok_context=tok_contexts_with_bos_and_eos,
                            tok_question=tok_question_with_bos_and_eos, qa=qa,
                            ctx_offset_dict=ctx_offset_dict,
                            ctx_end_offset_dict=ctx_end_offset_dict,
                            list_contexts=list_contexts,
                            list_word_in_question=list_word_in_question,
                            list_questions=list_questions,
                            list_word_in_context=list_word_in_context,
                            spans=spans, num_values=num_values,
                            question_ids=question_ids,
                            context_pos=context_pos, question_pos=question_pos,
                            context_ner=context_ner, question_ner=question_ner,
                            is_dev=is_dev,
                            ctx_ner_dict=ctx_ner_dict,
                            qst_ner_dict=qst_ner_dict,
                            psg_ctx=question_ids_to_passage_context[self.question_id])
            print("")
            spans = np.array(spans[:self.value_idx], dtype=np.int32)
            return RawTrainingData(
                list_contexts = list_contexts,
                list_word_in_question = list_word_in_question,
                list_questions = list_questions,
                list_word_in_context = list_word_in_context,
                spans = spans,
                question_ids = question_ids,
                context_pos = context_pos,
                question_pos = question_pos,
                context_ner = context_ner,
                question_ner = question_ner,
                question_ids_to_squad_question_id = question_ids_to_squad_question_id,
                question_ids_to_passage_context = question_ids_to_passage_context)

    def _create_predict_data_internal(self, context_str, question_str, is_dev):
        """Returns (contexts, word_in_question, questions, word_in_context, spans)
            contexts: list of lists of integer word ids
            word_in_question: list of lists of booleans indicating whether each
                word in the context is present in the question
            questions: list of lists of integer word ids
            word_in_context: list of lists of booleans indicating whether each
                word in the question is present in the context
            spans: numpy array of shape (num_samples, 2)
            question_ids: a list of ints that indicates which question the
                given sample is part of. this has the same length as
                |contexts| and |questions|. multiple samples may come from
                the same question because there are potentially multiple valid
                answers for the same question
        """
        print("Reading data from context_str, question_str", context_str, " :: " ,question_str)

        # data = json.load(data_file)
        # dataset = data["data"]
        num_values = 1
        spans = []
        list_contexts = []
        list_word_in_question = []
        list_questions = []
        list_word_in_context = []
        question_ids = []
        context_pos = []
        question_pos = []
        context_ner = []
        question_ner = []
        question_ids_to_squad_question_id = {}
        question_ids_to_passage_context = {}
        self.value_idx = 0

        # if dataset_id > 0 and _DEBUG_USE_ONLY_FIRST_ARTICLE:
        #     break
        # article = dataset[dataset_id]
        context = context_str

        print("Getting vocabulary")
        self.vocab = get_vocab(self.data_dir)

        self.nlp = spacy.load("en")
        self.tokenizer = create_tokenizer(self.nlp)
        self.nlp.tokenizer = self.tokenizer

        tok_context = self.nlp(context)
        tok_contexts_with_bos_and_eos = []
        ctx_ner_dict = self._get_ner_dict(tok_context)
        assert tok_context is not None
        ctx_offset_dict = {}
        ctx_end_offset_dict = {}
        word_idx_to_text_position = {}

        word_idx = 0
        for sentence in tok_context.sents:
            tok_contexts_with_bos_and_eos.append(_BOS)
            word_idx_to_text_position[word_idx] = \
                TextPosition(0, 0)
            word_idx += 1
            for token in sentence:
                tok_contexts_with_bos_and_eos.append(token)
                st = token.idx
                end = token.idx + len(token.text)
                ctx_offset_dict[st] = token
                ctx_end_offset_dict[end] = token
                word_idx_to_text_position[word_idx] = \
                    TextPosition(st, end)
                word_idx += 1
            tok_contexts_with_bos_and_eos.append(_EOS)
            word_idx_to_text_position[word_idx] = \
                TextPosition(0, 0)
            word_idx += 1

        self.question_id =0
        acceptable_gnd_truths = []
        # for answer in qa["answers"]:
        #     acceptable_gnd_truths.append(answer["text"])
        question_ids_to_passage_context[self.question_id] = \
            PassageContext(context, word_idx_to_text_position,
                           acceptable_gnd_truths)
        question = question_str
        squad_question_id = 0
        assert squad_question_id is not None
        question_ids_to_squad_question_id[self.question_id] = \
            squad_question_id
        tok_question = self.nlp(question)
        tok_question_with_bos_and_eos = []

        for sentence in tok_question.sents:
            tok_question_with_bos_and_eos.append(_BOS)
            for token in sentence:
                tok_question_with_bos_and_eos.append(token)
            tok_question_with_bos_and_eos.append(_EOS)

        qst_ner_dict = self._get_ner_dict(tok_question)
        assert tok_question is not None
        found_answer_in_context = False
        found_answer_in_context = self._maybe_add_samples_predict(
            tok_context=tok_contexts_with_bos_and_eos,
            tok_question=tok_question_with_bos_and_eos,
            ctx_offset_dict=ctx_offset_dict,
            ctx_end_offset_dict=ctx_end_offset_dict,
            list_contexts=list_contexts,
            list_word_in_question=list_word_in_question,
            list_questions=list_questions,
            list_word_in_context=list_word_in_context,
            spans=spans, num_values=num_values,
            question_ids=question_ids,
            context_pos=context_pos, question_pos=question_pos,
            context_ner=context_ner, question_ner=question_ner,
            is_dev=is_dev,
            ctx_ner_dict=ctx_ner_dict,
            qst_ner_dict=qst_ner_dict,
            psg_ctx=question_ids_to_passage_context[self.question_id])
        print("")
        spans = np.array(spans[:self.value_idx], dtype=np.int32)

        return RawTrainingData(
            list_contexts=list_contexts,
            list_word_in_question=list_word_in_question,
            list_questions=list_questions,
            list_word_in_context=list_word_in_context,
            spans=spans,
            question_ids=question_ids,
            context_pos=context_pos,
            question_pos=question_pos,
            context_ner=context_ner,
            question_ner=question_ner,
            question_ids_to_squad_question_id=question_ids_to_squad_question_id,
            question_ids_to_passage_context=question_ids_to_passage_context)

    def create_train_data(self, options):
        train_folder = os.path.join(self.data_dir, constants.TRAIN_FOLDER_NAME)
        dev_folder = os.path.join(self.data_dir, constants.DEV_FOLDER_NAME)
        train_files_wrapper = DatasetFilesWrapper(train_folder)
        dev_files_wrapper = DatasetFilesWrapper(dev_folder)

        if all([len(os.listdir(f)) > 0 for f in [train_folder, dev_folder]]):
            print("Train & dev data already exist.")
            return

        print("Getting vocabulary")
        self.vocab = get_vocab(self.data_dir)
        print("Finished getting vocabulary")
        self.nlp = spacy.load("en")
        self.tokenizer = create_tokenizer(self.nlp)
        self.nlp.tokenizer = self.tokenizer
        print("Getting DEV dataset")
        if options.exobrain_korean_dataset:
            dev_raw_data = self._create_train_data_internal(
            constants.DEV_SQUAD_KOREAN_FILE, is_dev=True)
        else:
            dev_raw_data = self._create_train_data_internal(
            constants.DEV_SQUAD_FILE, is_dev=True)
        print("Getting TRAIN dataset")
        if options.exobrain_korean_dataset:
            train_raw_data = self._create_train_data_internal(
            constants.TRAIN_SQUAD_KOREAN_FILE, is_dev=False)
        else:
            train_raw_data = self._create_train_data_internal(
            constants.TRAIN_SQUAD_FILE, is_dev=False)
        print("Num NER categories", self.ner_categories.get_num_categories())
        print("Num POS categories", self.pos_categories.get_num_categories())

        max_context_length = max(
                max([len(x) for x in train_raw_data.list_contexts]),
                max([len(x) for x in dev_raw_data.list_contexts]))

        max_question_length = max(
                max([len(x) for x in train_raw_data.list_questions]),
                max([len(x) for x in dev_raw_data.list_questions]))

        print("Saving TRAIN data")
        train_file_saver = DatasetFilesSaver(
                train_files_wrapper,
                max_context_length,
                max_question_length,
                self.vocab,
                train_raw_data)
        train_file_saver.save()

        print("Saving DEV data")
        dev_file_saver = DatasetFilesSaver(
                dev_files_wrapper,
                max_context_length,
                max_question_length,
                self.vocab,
                dev_raw_data)
        dev_file_saver.save()

        print("Finished creating training data!")

    def create_train_data_exobrain_korean(self, options):
        train_folder = os.path.join(self.data_dir, constants.TRAIN_FOLDER_NAME)
        dev_folder = os.path.join(self.data_dir, constants.DEV_FOLDER_NAME)
        train_files_wrapper = DatasetFilesWrapper(train_folder)
        dev_files_wrapper = DatasetFilesWrapper(dev_folder)

        if all([len(os.listdir(f)) > 0 for f in [train_folder, dev_folder]]):
            print("Train & dev data already exist.")
            return

        print("Getting vocabulary")
        self.vocab = get_vocab(self.data_dir)
        print("Finished getting vocabulary")
        self.nlp = spacy.load("en")
        self.tokenizer = create_tokenizer(self.nlp)
        self.nlp.tokenizer = self.tokenizer
        print("Getting DEV dataset")
        if options.exobrain_korean_dataset:
            dev_raw_data = self._create_train_data_internal(
            constants.DEV_SQUAD_KOREAN_FILE, is_dev=True)
        else:
            dev_raw_data = self._create_train_data_internal(
            constants.DEV_SQUAD_FILE, is_dev=True)
        print("Getting TRAIN dataset")
        if options.exobrain_korean_dataset:
            train_raw_data = self._create_train_data_internal(
            constants.TRAIN_SQUAD_KOREAN_FILE, is_dev=False)
        else:
            train_raw_data = self._create_train_data_internal(
            constants.TRAIN_SQUAD_FILE, is_dev=False)
        print("Num NER categories", self.ner_categories.get_num_categories())
        print("Num POS categories", self.pos_categories.get_num_categories())

        max_context_length = max(
                max([len(x) for x in train_raw_data.list_contexts]),
                max([len(x) for x in dev_raw_data.list_contexts]))

        max_question_length = max(
                max([len(x) for x in train_raw_data.list_questions]),
                max([len(x) for x in dev_raw_data.list_questions]))

        print("Saving TRAIN data")
        train_file_saver = DatasetFilesSaver(
                train_files_wrapper,
                max_context_length,
                max_question_length,
                self.vocab,
                train_raw_data)
        train_file_saver.save()

        print("Saving DEV data")
        dev_file_saver = DatasetFilesSaver(
                dev_files_wrapper,
                max_context_length,
                max_question_length,
                self.vocab,
                dev_raw_data)
        dev_file_saver.save()

        print("Finished converting exobrain korean dataset excel to squad json!")