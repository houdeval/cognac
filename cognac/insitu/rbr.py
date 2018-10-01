# !!! This should be a more general routine for ctd type data
#
# ------------------------- RBR data -----------------------------------
#

import pandas as pd

# containment and delegation

class rbr(object):
    """ Contains DST inclino data
    """
    def __init__(self, file=None):
        if file is not None:
            self.d = self._read(file)

    def __str__(self):
        return self.d.__str__()

    def __getitem__(self, item):
        if item is 'time':
            # time or depth?
            return self.d.index
        else:
            return getattr(self.d, item)

    def _read(self, file):
        d = pd.read_table(file,
                          names=['temperature','pressure','sea_pressure','depth'],
                          skiprows=[0], sep=',', decimal='.', index_col=0, parse_dates=[0])
        # dtype={'temp': np.float64} is ignored if passed to read_table
        d.temperature = d.temperature.astype(float)
        #d = d.set_index('time')
        d.index.name='time'
        return d

    def trim(self, t0=None, t1=None, d=None):
        ''' select data between t0 and t1 '''
        if any([t0, t1]):
            self.d = self.d[t0:t1]
        elif 'deployment' in str(type(d)):
            self.d = self.d[d.start.time:d.end.time]
        return self
