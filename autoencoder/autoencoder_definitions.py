"""
Created on Sat Jun 20 12:30:08 2020

@author: scantleb
@brief: AutoEncoder class definition

Autoencoders learn a mapping from a high dimensional space to a lower
dimensional space, as well as the inverse.
"""
from abc import abstractmethod
from functools import reduce
from operator import mul

import numpy as np
import tensorflow as tf
from tensorflow.keras.layers import Input, Conv3D, Flatten, Dense, \
    Reshape

from layers.layers import tf_transition_block, tf_inverse_transition_block, \
    tf_dense_block, generate_activation_layers


class AutoEncoderBase(tf.keras.Model):
    """Abstract parent class for autoencoders."""

    def __init__(self,
                 dims,
                 encoding_size=10,
                 optimiser='sgd',
                 loss='mse',
                 hidden_activation='sigmoid',
                 final_activation='sigmoid',
                 **opt_args):
        """Setup and compilation of autoencoder.

        Arguments:
            optimiser: any keras optimisation class
            loss: any keras loss fuction (or string reference), or or
                'unbalanced'/'composite_mse' (custom weighted loss functions)
            hidden_activation: activation function for hidden layers
            final_activation: activation function for reconstruction layer
            opt_args: arguments for the keras optimiser (see keras
                documentation)
        """

        self.initialiser = tf.keras.initializers.HeNormal()  # weights init

        # Abstract method should be implemented in child class
        self.input_image, self.encoding, self.reconstruction = \
            self._construct_layers(
                dims=dims,
                encoding_size=encoding_size,
                hidden_activation=hidden_activation,
                final_activation=final_activation)

        # If optimiser is a string, turn it into a keras optimiser object
        if isinstance(optimiser, str):
            optimiser = tf.keras.optimizers.get(optimiser).__class__

        # Composite mse requires an extra weight input
        inputs = [self.input_image]
        if loss == 'composite_mse':
            self.frac = Input(shape=(1,), dtype=tf.float32, name='frac')
            inputs.append(self.frac)

        super().__init__(
            inputs=inputs,
            outputs=[self.reconstruction, self.encoding]
        )

        metrics = {'reconstruction': [mae, nonzero_mae, zero_mae, zero_mse,
                                      nonzero_mse]}

        if loss == 'composite_mse':
            self.add_loss(composite_mse(
                self.input_image, self.reconstruction, self.frac))
            self.compile(
                optimizer=optimiser(**opt_args),
                metrics=metrics
            )
        else:
            self.compile(
                optimizer=optimiser(**opt_args),
                loss={'reconstruction': loss,
                      'encoding': None},
                metrics=metrics
            )

        # Bug with add_loss puts empty dict at the end of model._layers which
        # interferes with some functionality (such as
        # tf.keras.utils.plot_model)
        self._layers = [layer for layer in self._layers if isinstance(
            layer, tf.keras.layers.Layer)]

    @abstractmethod
    def _construct_layers(self, dims, encoding_size, hidden_activation,
                          final_activation):
        """Setup for autoencoder architecture (abstract method).

        Arguments:
            dims: dimentionality of inputs
            encoding_size: size of bottleneck
            hidden_activation: activation function for hidden layers
            final_activation: activation function for final layer

        Returns:
            Tuple containing the input layer, the encoding layer, and the
            reconstruction layer of the autoencoder.

        Raises:
            NotImplementedError: if this method is not overridden by a class
                inheriting from this (abstract) class, or if this (abstract)
                class is initialised explicitly.
        """

        raise NotImplementedError('construct_layers must be implemented '
                                  'in classes inherited from AutoEncoderBase. '
                                  'AutoEncoderBase is an abstract class and '
                                  'should not be initialised.')


class DenseAutoEncoder(AutoEncoderBase):
    """Convolutional autoencoder with Dense connectivity."""

    def _construct_layers(self, dims, encoding_size, hidden_activation,
                          final_activation):
        """Overloaded method; see base class (AutoeEncoderBase)"""

        encoding_activation_layer, _ = generate_activation_layers(
            'encoding', hidden_activation, append_name_info=False)
        decoding_activation_layer, _ = generate_activation_layers(
            'decoding', hidden_activation, append_name_info=False)

        input_image = Input(
            shape=dims, dtype=tf.float32, name='input_image')

        # Hidden layers
        x = tf_dense_block(input_image, 8, "db_1", hidden_activation)
        x = tf_transition_block(x, 0.5, "tb_1", hidden_activation)

        x = tf_dense_block(x, 8, "db_2", hidden_activation)
        x = tf_transition_block(x, 0.5, 'tb_2', hidden_activation)

        final_shape = x.shape
        x = Flatten(data_format='channels_first')(x)

        x = Dense(encoding_size, kernel_initializer=self.initialiser)(x)
        encoding = encoding_activation_layer(x)

        decoding = Dense(reduce(mul, final_shape[1:]),
                         kernel_initializer=self.initialiser)(encoding)
        decoding = decoding_activation_layer(decoding)

        reshaped = Reshape(final_shape[1:])(decoding)

        x = tf_inverse_transition_block(reshaped, 0.5, 'itb_1',
                                        hidden_activation)
        x = tf_dense_block(x, 8, 'idb_1', hidden_activation)

        x = tf_inverse_transition_block(x, 0.5, 'itb_2', hidden_activation)
        x = tf_dense_block(x, 8, 'idb_2', hidden_activation)

        reconstruction = Conv3D(dims[0], 3,
                                activation=final_activation,
                                kernel_initializer=self.initialiser,
                                data_format='channels_first',
                                use_bias=False,
                                padding='SAME', name='reconstruction')(x)

        return input_image, encoding, reconstruction


class SingleLayerAutoEncoder(AutoEncoderBase):
    """Single layer nonconvolutional autoencoder."""

    def _construct_layers(self, dims, encoding_size, hidden_activation,
                          final_activation):
        """Overloaded method; see base class (AutoeEncoderBase)"""

        encoding_activation_layer, _ = generate_activation_layers(
            'encoding', hidden_activation, append_name_info=False)

        input_image = Input(shape=dims, dtype=tf.float32,
                            name='input_image')
        x = Flatten()(input_image)

        x = Dense(encoding_size)(x)
        encoding = encoding_activation_layer(x)

        x = Dense(np.prod(dims),
                  activation=final_activation)(encoding)
        reconstruction = Reshape(dims, name='reconstruction')(x)

        return input_image, encoding, reconstruction


def nonzero_mse(target, reconstruction):
    """Mean squared error for non-zero values in the target matrix

    Finds the mean squared error for all parts of the input tensor which
    are not equal to zero.

    Arguments:
        target: input tensor
        reconstruction: output tensor of the autoencoder

    Returns:
        Mean squared error for all non-zero entries in the target
    """
    mask = tf.cast(tf.not_equal(target, 0), tf.float32)
    masked_difference = (target - reconstruction) * mask
    return tf.reduce_mean(tf.square(masked_difference))


def zero_mse(target, reconstruction):
    """Mean squared error for zero values in the target matrix

    Finds the mean squared error for all parts of the input tensor which
    are equal to zero.

    Arguments:
        target: input tensor
        reconstruction: output tensor of the autoencoder

    Returns:
        Mean squared error for all zero entries in the target
    """
    mask = tf.cast(tf.equal(target, 0), tf.float32)
    masked_difference = (target - reconstruction) * mask
    return tf.reduce_mean(tf.square(masked_difference))


def composite_mse(target, reconstruction, ratio):
    """Weighted mean squared error of nonzero-only and zero-only inputs.

    Finds the MSE between the autoencoder reconstruction and the nonzero
    entries of the input, the MSE between the reconstruction and the zero
    entries of the input and gives the weighted average of the two.

    Arguments:
        target: input tensor
        reconstruction: output tensor of the autoencoder
        ratio: desired ratio of nonzero : zero
        _num: this should be a tf.constant(1., dtype=float32) [used to ensure
            we can use plot_model]

    Returns:
        Average weighted by:

            ratio/(1+ratio)*nonzero_mse + 1/(1+ratio)*zero_mse

        where nonzero_mse and zero_mse are the MSE for the nonzero and zero
        parts of target respectively.
    """
    frac = tf.divide(ratio, 1. + ratio)
    return tf.math.add(
        tf.math.multiply(frac, nonzero_mse(target, reconstruction)),
        tf.math.multiply(1. - frac, zero_mse(target, reconstruction)))


def mae(target, reconstruction):
    """Mean absolute error loss function.

    Arguments:
        target: input tensor
        reconstruction: output tensor of the autoencoder

    Returns:
        Tensor containing the mean absolute error between the target and
        the reconstruction.
    """
    return tf.reduce_mean(tf.abs(target - reconstruction))


def zero_mae(target, reconstruction):
    """Mean absolute error loss function target values are zero.

    Arguments:
        target: input tensor
        reconstruction: output tensor of the autoencoder

    Returns:
        Tensor containing the mean absolute error between the target and
        the reconstruction where the mean is taken over values where
        the target is equal to zero.
        This can be NaN if there are no inputs equal to zero.
    """
    mask = tf.cast(tf.equal(target, 0), tf.float32)
    masked_diff = (target - reconstruction) * mask
    abs_diff = tf.abs(masked_diff)
    return tf.divide(tf.reduce_sum(abs_diff), tf.reduce_sum(mask))


def nonzero_mae(target, reconstruction):
    """Mean absolute error loss function target values are not zero.

    Arguments:
        target: input tensor
        reconstruction: output tensor of the autoencoder

    Returns:
        Tensor containing the mean absolute error between the target and
        the reconstruction where the mean is taken over values where
        the target is not zero.
        This can be NaN if there are no nonzero inputs.
    """
    mask = 1 - tf.cast(tf.equal(target, 0), tf.float32)
    mask_sum = tf.reduce_sum(mask)
    masked_diff = (target - reconstruction) * mask
    abs_diff = tf.abs(masked_diff)
    return tf.divide(tf.reduce_sum(abs_diff), mask_sum)
