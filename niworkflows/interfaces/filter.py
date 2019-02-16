#!/usr/bin/env python
# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

"""
Temporal filtering operations for the image processing system
"""

import numpy as np
import nibabel as nb
from scipy import signal


def _validate_passband(filter_type, passband):
    """Verify that the filter passband is reasonably formulated.

    Parameters
    ----------
    filter_type: str
        String indicating the type of filter.
    passband: tuple
        2-tuple indicating high-pass and low-pass cutoffs for the filter.
    """
    if passband[0] > passband[1]:
        raise ValueError(
            '\n'
            'High-pass cutoff must be less than low-pass cutoff\n'
            'for the selected filter type.\n'
            '==================================================\n'
            'Filter class:     {0}\n'
            'High-pass cutoff: {1[0]}\n'
            'Low-pass cutoff:  {1[1]}\n'.format(filter_type, passband))


def _get_fsl_passband(passband, sampling_rate):
    """
    Convert the passband to a form that FSL can understand.
    For use when filtering with a Gaussian kernel.
    1) Convert the cutoff frequencies from Hz (cycles per second) to cycles
       per repetition.
    2) Convert from frequency cutoff (in Hz) to cycle cutoff (in s). 
    3) Then, determine how many cycles of the cutoff per repetition.

    Parameters
    ----------
    passband: tuple
        2-tuple indicating high-pass and low-pass cutoffs for the filter.
    sampling_rate: float
        Repetition time.

    Returns
    -------
    tuple
        FSL-compatible passband.
    """
    passband_frequency = (0, -1)
    passband_frequency[0] = (1/passband[0])/(2 * sampling_rate)
    if passband[1] == 'nyquist':
        passband_frequency[1] = -1
    else:
        passband_frequency[1] = 1/passband[1]/(2 * sampling_rate)
    return passband_frequency


def _normalise_passband(passband, nyquist):
    """Normalise the passband according to the Nyquist frequency."""
    passband_norm = (0, 1)

    passband_norm[0] = float(passband[0])/nyquist
    if passband[1] == 'nyquist':
        passband_norm[1] = 1
    else:
        passband_norm[1] = float(passband[1])/nyquist
    return passband_norm


def _get_norm_passband(passband, sampling_rate):
    """Convert the passband to normalised form.

    Parameters
    ----------
    passband: tuple
        2-tuple indicating high-pass and low-pass cutoffs for the filter.
    sampling_rate: float
        Repetition time.

    Returns
    -------
    tuple
        Cutoff frequencies normalised between 0 and Nyquist.
    None or str
        Indicator of whether the filter is permissive at high or low
        frequencies. If None, this determination is 
    """
    nyquist = 0.5 * sampling_rate
    passband_norm = _normalise_passband(passband, nyquist)

    if passband_norm[1] >= 1:
        filter_pass = 'highpass'
    if passband_norm[0] <= 0:
        if filter_pass == 'highpass':
            raise ValueError(
                '\n'
                'Permissive filter for the specified sampling rate.\n'
                '==================================================\n'
                'Sampling rate:     {0}\n'
                'Nyquist frequency: {1}\n'
                'High-pass cutoff:  {2[0]}\n'
                'Low-pass cutoff:   {2[1]}\n'.format(
                    sampling_rate, nyquist, passband))
        passband_norm[0] = 0
        filter_pass = 'lowpass'

    if filter_pass == 'highpass':
        passband_norm = passband_norm[0]
    elif filter_pass == 'lowpass':
        passband_norm = passband_norm[1]
    elif passband_norm[0] > passband_norm[1]:
        filter_pass = 'bandstop'
    else:
        filter_pass = 'bandpass'
    return passband_norm, filter_pass


def _unfold_image(img, mask=None):
    """Unfold a four-dimensional time series into two dimensions.

    Parameters
    ----------
    img: nibabel NIfTI object
        NIfTI object corresponding to the 4D time series to be unfolded.
    mask: nibabel NIfTI object
        Mask indicating the spatial extent of the unfolding. To unfold
        only brain voxels, for instance, this should be a brain mask.

    Returns
    numpy array
        2-dimensional numpy array with rows equal to frames in the time
        series and columns equal to voxels in the mask..
    """
    if mask is not None:
        return img.get_fdata()[mask.get_data().astype('bool')]
    else:
        return img.get_fdata().reshape([-1, img.shape[3]])


def _fold_image(data, template, mask=None):
    """Fold a 2D numpy array into a 4D NIfTI time series.

    Parameters
    ----------
    data: numpy array
        2-dimensional numpy array to be folded.
    template: nibabel NIfTI object
        NIfTI object that provides header and affine information for the
        folded dataset. This might, for instance, be the original 4D time
        series that was previously unfolded into the 2D data array.
    mask: nibabel NIfTI object
        Mask indicating the spatial extent of the unfolded 2D data.

    Returns
    -------
    nibabel NIfTI object
    """
    if mask is not None:
        data_folded = np.zeros(shape=template.shape)
        data_folded[mask.get_data().astype('bool')] = data
    else:
        data_folded = data.reshape(template.shape)
    return nib.Nifti1Image(dataobj=data_folded,
                           affine=template.affine,
                           header=template.header)


def general_filter(data,
                   sampling_rate,
                   filter_type='butterworth',
                   filter_order=1,
                   passband=(0.01, 0.08),
                   ripple_pass=5,
                   ripple_stop=20):
    """Temporal filtering for any data array.

    Parameters
    ----------
    data: numpy array
        2D data array to be filtered. The input should be transformed so that
        the axis to be filtered (typically time) is the second axis and all
        remaining dimensions are unfolded into the first axis.
    sampling_rate: float
        Repetition time or sampling rate of the data along the filter axis.
    filter_type: str
        Filter class: one of `butterworth`, `chebyshev1`, `chebyshev2`, and
        `elliptic`. Note that Chebyshev and elliptic filters require
        specification of appropriate ripples.
    filter_order: int
        Order of the filter. Note that the output filter has double the
        specified order; this prevents phase-shifting of the data.
    passband: tuple
        2-tuple indicating high-pass and low-pass cutoffs for the filter.
    ripple_pass: float
        Passband ripple parameter. Required for elliptic and type I Chebyshev
        filters.
    ripple_stop: float
        Stopband ripple parameter. Required for elliptic and type II Chebyshev
        filters.

    Returns
    -------
    numpy array
        The filtered input data array.
    """
    passband_norm, filter_pass = _get_norm_passband(passband, sampling_rate)

    if filter_type == 'butterworth':
        filt = signal.butter(N=filter_order,
                             Wn=passband_norm,
                             btype=filter_pass)
    elif filter_type == 'chebyshev1':
        filt = signal.cheby1(N=filter_order,
                             rp=ripple_pass,
                             Wn=passband_norm,
                             btype=filter_pass)
    elif filter_type == 'chebyshev2':
        filt = signal.cheby2(N=filter_order,
                             rs=ripple_stop,
                             Wn=passband_norm,
                             btype=filter_pass)
    elif filter_type == 'elliptic':
        filt = signal.ellip(N=filter_order,
                            rp=ripple_pass,
                            rs=ripple_stop,
                            Wn=passband_norm,
                            btype=filter_pass)
    ##########################################################################
    #TODO this block needs some work.
    # Mask nans and filter the data. Should probably interpolate over those
    # nans though.
    #TODO need to do something more reasonable to the nans  as this will
    #     distort the filter like hell.
    #     serial transposition is demeaning the data so that the filter is
    #     well-behaved. mean is added back at the end.
    #     also necessary to broadcast arrays.
    #TODO also need to decide what to do about means. filter will be incorrect
    #     unless data are demeaned, but the mean should potentially omit
    #     censored values
    ##########################################################################
    mask = np.isnan(data)
    data[mask] =   0
    colmeans = data.mean(1)
    data = signal.filtfilt(filt[0],filt[1],(data.T - colmeans).T,
                           method='gust')
    data[mask] = np.nan
    return (data.T + colmeans).T
