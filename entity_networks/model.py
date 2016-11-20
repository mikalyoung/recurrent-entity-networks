from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

import numpy as np
import tensorflow as tf

from keras.metrics import categorical_accuracy

from entity_networks.activations import prelu
from entity_networks.dynamic_memory_cell import DynamicMemoryCell

class Model(object):

    def __init__(self, story, query, answer, story_length, query_length, batch_size, is_training=True):
        self.batch_size = batch_size
        self.vocab_size = 22
        self.max_story_length = 65
        self.max_query_length = 4

        self.window_size = 5
        self.num_windows = self.max_story_length // self.window_size

        self.num_blocks = 20
        self.num_units_per_block = 100

        embedding_params = tf.get_variable('embedding_params',
            shape=[self.vocab_size, self.num_units_per_block],
            initializer=tf.random_uniform_initializer(minval=-1, maxval=1))

        story_embedding = tf.nn.embedding_lookup(embedding_params, story)
        query_embedding = tf.nn.embedding_lookup(embedding_params, query)
        answer_hot = tf.one_hot(answer, self.vocab_size)

        # Input Module
        state = self.get_input(story_embedding)

        # Dynamic Memory
        memory_cell = DynamicMemoryCell(self.num_blocks, self.num_units_per_block, activation=prelu)
        output, last_state = tf.nn.dynamic_rnn(memory_cell, state,
            initial_state=memory_cell.zero_state(batch_size, dtype=tf.float32),
            sequence_length=self.get_sequence_length(state))

        # Output Module
        self.output = self.get_output(last_state, query_embedding)

        # Loss
        with tf.variable_scope('Loss'):
            # XXX: We assume cross-entropy loss, even though the logits are never scaled.
            cross_entropy = tf.nn.softmax_cross_entropy_with_logits(self.output, answer_hot)
            self.loss = tf.reduce_mean(cross_entropy)

        # Accuracy
        with tf.variable_scope('accuracy'):
            self.accuracy = categorical_accuracy(answer_hot, self.output)

        # Summaries
        tf.contrib.layers.summarize_tensor(self.loss)
        tf.contrib.layers.summarize_tensor(self.accuracy)

        # Optimization
        if is_training:
            self.global_step = tf.contrib.framework.get_or_create_global_step()
            self.learning_rate = tf.placeholder(tf.float32, shape=())
            self.train_op = tf.contrib.layers.optimize_loss(
                self.loss,
                self.global_step,
                learning_rate=self.learning_rate,
                clip_gradients=40.0,
                optimizer='Adam')

    def get_sequence_length(self, sequence):
        used = tf.sign(tf.reduce_max(tf.abs(sequence), reduction_indices=2))
        length = tf.reduce_sum(used, reduction_indices=1)
        length = tf.cast(length, tf.int32)
        return length

    def get_input(self, story_embedding):
        print(story_embedding)
        story_embedding = tf.reshape(story_embedding,
            shape=[self.batch_size, self.max_story_length, self.num_units_per_block])
        print(story_embedding)
        story_embedding_window = tf.reshape(story_embedding,
            shape=[self.batch_size, self.num_windows, self.window_size, self.num_units_per_block])
        print(story_embedding_window)
        mask = tf.get_variable('mask',
            shape=[self.window_size, self.num_units_per_block],
            initializer=tf.contrib.layers.variance_scaling_initializer())
        state = tf.reduce_sum(story_embedding_window * mask, reduction_indices=[2])
        return state

    def get_output(self, last_state, query_embedding):
        """
        Implementation of Section 2.3, Equation 6. This module is also described here:
        [End-To-End Memory Networks](https://arxiv.org/abs/1502.01852).
        """
        # XXX: We assume the query is convert into a bag-of-words representation.
        q = tf.reduce_sum(query_embedding, reduction_indices=[1])
        last_states = tf.split(1, self.num_blocks, last_state)

        u_j = []
        for h_j in last_states:
            # XXX: We assume the matrix multiplication is correct as the inner product would be scalar.
            p_j_inner = tf.batch_matmul(tf.expand_dims(q, -1), tf.expand_dims(h_j, 1))
            p_j = tf.nn.softmax(p_j_inner) # Attention
            p_j = tf.batch_matmul(p_j, tf.expand_dims(h_j, -1))
            u_j.append(p_j)
        u_j = tf.concat(2, u_j)
        u = tf.reduce_sum(u_j, reduction_indices=[-1]) # Response Vector

        R = tf.get_variable('R',
            shape=[self.num_units_per_block, self.vocab_size],
            initializer=tf.contrib.layers.variance_scaling_initializer()) # Decoder Matrix
        H = tf.get_variable('H',
            shape=[self.num_units_per_block, self.num_units_per_block],
            initializer=tf.contrib.layers.variance_scaling_initializer())

        # XXX: We assume \phi in Equation 6 is the same \phi in Equation 3, which is PReLU for bAbI.
        y = tf.matmul(prelu(q + tf.matmul(u, H)), R)
        return y

    @property
    def num_parameters(self):
        return np.sum([np.prod(tvar.get_shape().as_list()) for tvar in tf.trainable_variables()])