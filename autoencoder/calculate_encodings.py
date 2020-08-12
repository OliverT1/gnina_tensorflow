#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 10 11:11:57 2020

@author: scantleb
@brief: Use trained autoencoder to calculate encodings for gnina inputs.
"""

import torch
import molgrid
import tensorflow as tf

from autoencoder import autoencoder_definitions
from collections import defaultdict, deque
from pathlib import Path
from utilities import gnina_embeddings_pb2, gnina_functions


def calculate_encodings(encoder, gmaker, input_tensor, data_root, types_file,
                        save_path, rotate=False, verbose=False):
    """Calculates encodings for gnina inputs.

    Uses trained AutoEncoder object to calculate the encodings of all gnina
    inputs in types_file, which are then serialised to a protobuf message.

    Arguments:
        encoder: trained AutoEncoder object
        data_root: path to which all paths in types file are relative
        types_file: contains list of gninatypes paths, storing the gnina-style
            inputs which are to be encoded in a lower dimensional space
        rotate: whether to randomly rotate gnina inputs in increments of 15
            degrees

    Returns:
        Dictionary of serialised protein protobuf messages with structure
        defined in gnina_encodings.proto. The structure is:

            {receptor_path : serialised_protobuf_messages (1 per ligand)}
    """

    def get_paths():
        """Reads types file to give path, label and indexing information.

        Returns a dictionary mapping of { global_idx: (label, rec, lig) } where
        global_idx is the position of the receptor/ligand pair in the types
        file, label is in {0, 1}, and rec and lig are the relative paths to
        the receptor and ligand gninatypes files, respectively.
        """
        paths = {}
        recs = set()
        current_rec = None
        with open(types_file, 'r') as f:
            for idx, line in enumerate(f.readlines()):
                chunks = line.strip().split()
                label = int(chunks[0])
                rec = chunks[1]
                lig = chunks[2]
                if idx == 0:
                    current_rec = rec
                elif rec != current_rec and rec in recs:
                    # TODO: on-the-fly types file grouping by receptor in the
                    # case that types file is not grouped by receptor
                    raise RuntimeError(
                        'Types file must be grouped by receptor')
                paths[idx] = (label, rec, lig)
        return paths

    def write_encodings_to_disk(rec, encodings, encodings_dir):
        rec_msg = gnina_embeddings_pb2.protein()
        rec_msg.path = rec
        for label, lig_path, lig_encoding in encodings:
            lig_msg = rec_msg.ligand.add()
            lig_msg.path = lig_path
            lig_msg.embedding.extend(lig_encoding)
            lig_msg.label = label

        fname = Path(rec).stem + '.bin'
        with open(encodings_dir / fname, 'wb') as f:
            f.write(rec_msg.SerializeToString())

    # Setup for gnina
    batch_size = input_tensor.shape[0]
    e = molgrid.ExampleProvider(data_root=str(data_root), balanced=False,
                                shuffle=False)
    e.populate(str(types_file))

    # Need a dictionary mapping {global_idx: (label, rec, lig) where global_idx
    # is the position of the receptor/ligand pair in the types file
    paths = get_paths()
    total_size = len(paths)
    iterations = total_size // batch_size

    # Inference (obtain encodings)
    current_rec = paths[0][1]
    encodings = []
    encodings_dir = Path(save_path) / 'encodings'
    encodings_dir.mkdir(exist_ok=True, parents=True)
    for iteration in range(iterations):
        if verbose:
            print('Iteration {0} of {1}'.format(iteration + 1, iterations))
        batch = e.next_batch(batch_size)
        gmaker.forward(batch, input_tensor, 0, random_rotation=rotate)
        _, encodings_numpy = encoder.predict_on_batch(input_tensor.tonumpy())
        for batch_idx in range(batch_size):
            global_idx = iteration * batch_size + batch_idx
            label, rec, lig = paths[global_idx]
            if rec != current_rec:
                write_encodings_to_disk(current_rec, encodings, encodings_dir)
                encodings = []
                current_rec = rec
            encodings.append((label, lig, encodings_numpy[batch_idx, :]))

    remainder = total_size % batch_size
    batch = e.next_batch(batch_size)
    gmaker.forward(batch, input_tensor, 0, random_rotation=rotate)
    _, encodings_numpy = encoder.predict_on_batch(input_tensor.tonumpy())

    for batch_idx in range(remainder):
        global_idx = iterations * batch_size + batch_idx
        label, rec, lig = paths[global_idx]
        if rec != current_rec:
            write_encodings_to_disk(current_rec, encodings, encodings_dir)
            encodings = []
            current_rec = rec
        encodings.append((label, lig, encodings_numpy[batch_idx, :]))

    if len(encodings):  # Encodings that have not been saved (final receptor)
        write_encodings_to_disk(current_rec, encodings, encodings_dir)


if __name__ == '__main__':
    # Parse and sanitise command line args
    autoencoder, args = autoencoder_definitions.parse_command_line_args('test')
    autoencoder.summary()

    molgrid.set_gpu_enabled(1 - args.use_cpu)

    tf.keras.backend.clear_session()

    # Setup libmolgrid to feed Examples into tensorflow objects
    e = molgrid.ExampleProvider(
        data_root=str(args.data_root), balanced=False, shuffle=False)
    e.populate(str(args.test))

    gmaker = molgrid.GridMaker(
        binary=args.binary_mask,
        dimension=args.dimension,
        resolution=args.resolution)

    dims = gmaker.grid_dimensions(e.num_types())
    tensor_shape = (args.batch_size,) + dims
    input_tensor = molgrid.MGrid5f(*tensor_shape)

    with gnina_functions.Timer() as t:
        calculate_encodings(
            autoencoder, gmaker, input_tensor, args.data_root, args.test,
            save_path=args.save_path, rotate=False, verbose=True)
    print('Encodings calculated and saved in {} s'.format(t.interval))
    print('Encodings written to {}'.format(args.save_path))