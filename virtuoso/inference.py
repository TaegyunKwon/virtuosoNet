import numpy as np
import argparse
import torch

from .constants import *
from ..pyScoreParser.data_class import ScoreData
from ..pyScoreParser.feature_extraction import ScoreExtractor
from pathlib import Path

def scale_model_prediction_to_original(prediction, means, stds, loss_type='MSE'):
    for i in range(len(stds)):
        for j in range(len(stds[i])):
            if stds[i][j] < 1e-4:
                stds[i][j] = 1
    prediction = np.squeeze(np.asarray(prediction.cpu()))
    num_notes = len(prediction)
    if loss_type == 'MSE':
        for i in range(11):
            prediction[:, i] *= stds[1][i]
            prediction[:, i] += means[1][i]
        for i in range(11, 15):
            prediction[:, i] *= stds[1][i+4]
            prediction[:, i] += means[1][i+4]
    elif loss_type == 'CE':
        prediction_in_value = np.zeros((num_notes, 16))
        for i in range(num_notes):
            bin_range_start = 0
            for j in range(15):
                feature_bin_size = len(BINS[j]) - 1
                feature_class = np.argmax(
                    prediction[i, bin_range_start:bin_range_start + feature_bin_size])
                feature_value = (BINS[j][feature_class] +
                                 BINS[j][feature_class + 1]) / 2
                prediction_in_value[i, j] = feature_value
                bin_range_start += feature_bin_size
            prediction_in_value[i, 15] = prediction[i, -1]
        prediction = prediction_in_value

    return prediction


def load_file_and_generate_performance(model, path_name, args, trill_model, hier_model=None, return_features=False):
    composer=args.composer
    z=args.latent
    start_tempo=args.startTempo
    path_name = Path(path_name)
    if path_name.suffix not in ('xml', 'musicxml'):
        path_name = path_name / 'xml.xml'
        if not path_name.exists():
            path_name = path_name.parent / 'musicxml_cleaned.musicxml'

    score_features = model.score_feature_keys
    score_data = ScoreData(path_name)
    feature_extractor = ScoreExtractor()
    test_x = feature_extractor.extract_score_features(score_data)
    test_x, xml_notes, xml_doc, edges, note_locations = xml_matching.read_xml_to_array(path_name, means, stds,
                                                                                       start_tempo, composer,
                                                                                       vel_pair)
    batch_x = torch.Tensor(test_x)
    num_notes = len(test_x)
    input_y = torch.zeros(1, num_notes, NUM_OUTPUT).to(DEVICE)

    if type(z) is dict:
        initial_z = z['z']
        qpm_change = z['qpm']
        z = z['key']
        batch_x[:, QPM_PRIMO_IDX] = batch_x[:, QPM_PRIMO_IDX] + qpm_change
    else:
        initial_z = 'zero'

    if IN_HIER:
        batch_x = batch_x.to(DEVICE).view(1, -1, hier_model.input_size)
        graph = edges_to_matrix(edges, batch_x.shape[1])
        MODEL.is_teacher_force = False
        if type(initial_z) is list:
            hier_z = initial_z[0]
            final_z = initial_z[1]
        else:
            # hier_z = [z] * HIER_MODEL_PARAM.encoder.size
            hier_z = 'zero'
            final_z = initial_z
        hier_input_y = torch.zeros(1, num_notes, hier_model.output_size)
        hier_output, _ = run_model_in_steps(
            batch_x, hier_input_y, graph, note_locations, initial_z=hier_z, model=hier_model)
        if 'measure' in args.hierCode:
            hierarchy_numbers = [x.measure for x in note_locations]
        else:
            hierarchy_numbers = [x.section for x in note_locations]
        hier_output_spanned = hier_model.span_beat_to_note_num(
            hier_output, hierarchy_numbers, len(test_x), 0)
        combined_x = torch.cat((batch_x, hier_output_spanned), 2)
        prediction, _ = run_model_in_steps(
            combined_x, input_y, graph, note_locations, initial_z=final_z, model=model)
    else:
        if type(initial_z) is list:
            initial_z = initial_z[0]
        batch_x = batch_x.to(DEVICE).view(1, -1, NUM_INPUT)
        graph = edges_to_matrix(edges, batch_x.shape[1])
        prediction, _ = run_model_in_steps(
            batch_x, input_y, graph, note_locations, initial_z=initial_z, model=model)

    trill_batch_x = torch.cat((batch_x, prediction), 2)
    trill_prediction, _ = run_model_in_steps(trill_batch_x, torch.zeros(
        1, num_notes, cons.num_trill_param), graph, note_locations, model=trill_model)

    prediction = torch.cat((prediction, trill_prediction), 2)
    prediction = scale_model_prediction_to_original(prediction, means, stds)

    output_features = xml_matching.model_prediction_to_feature(prediction)
    output_features = xml_matching.add_note_location_to_features(
        output_features, note_locations)
    if return_features:
        return output_features

    output_xml = xml_matching.apply_tempo_perform_features(xml_doc, xml_notes, output_features, start_time=1,
                                                           predicted=True)
    output_midi, midi_pedals = xml_matching.xml_notes_to_midi(output_xml)
    piece_name = path_name.split('/')
    save_name = 'test_result/' + \
        piece_name[-2] + '_by_' + args.modelCode + '_z' + str(z)

    perf_worm.plot_performance_worm(output_features, save_name + '.png')
    xml_matching.save_midi_notes_as_piano_midi(output_midi, midi_pedals, save_name + '.mid',
                                               bool_pedal=args.boolPedal, disklavier=args.disklavier)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-mode", "--sessMode", type=str,
                        default='train', help="train or test or testAll")
    parser.add_argument("-path", "--test_path", type=str,
                        default="./test_pieces/bps_5_1/", help="folder path of test mat")
    parser.add_argument("-tempo", "--startTempo", type=int,
                        default=0, help="start tempo. zero to use xml first tempo")

    model = load_model
    load_file_and_generate_performance(args.test_path, args)