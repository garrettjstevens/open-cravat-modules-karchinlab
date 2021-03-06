import os
import argparse
import sys
import sqlite3
import re
import time
import logging
import yaml
from cravat import CravatReader
from cravat import CravatWriter

#test

class Aggregator (object):
    
    cr_type_to_sql = {'string':'text',
                      'int':'integer',
                      'float':'real'}
    commit_threshold = 10000
    
    def __init__(self, cmd_args):
        self.annotators = []
        self.ipaths = {}
        self.readers = {}
        self.base_fpath = None
        self.level = None
        self.input_dir = None
        self.input_base_fname = None
        self.output_dir = None
        self.output_base_fname = None
        self.key_name = None
        self.table_name = None
        self.opts = None
        self.base_prefix = 'base'
        self.base_dir = os.path.abspath(__file__)
        self.parse_cmd_args(cmd_args)
        self._setup_logger()
        self._read_opts()
        
    def parse_cmd_args(self, cmd_args):
        parser = argparse.ArgumentParser()
        parser.add_argument('path',
                            help='Path to this aggregator module')
        parser.add_argument('-i',
                            dest='input_dir',
                            required=True,
                            help='Directory containing annotator outputs')
        parser.add_argument('-l',
                            dest='level',
                            required= True,
                            help='Level to aggregate')
        parser.add_argument('-n',
                            dest='name',
                            required=True,
                            help='Name of run')
        parser.add_argument('-d',
                            dest='output_dir',
                            help='Directory for aggregator output. '\
                                 +'Default is input directory.')
        parser.add_argument('-x',
                            dest='delete',
                            action='store_true',
                            help='Deletes the existing one and creates ' +\
                                 'a new one.')
        parsed = parser.parse_args(cmd_args)
        self.level = parsed.level
        self.name = parsed.name
        self.input_dir = os.path.abspath(parsed.input_dir)
        if parsed.output_dir:
            self.output_dir = parsed.output_dir
        else:
            self.output_dir = self.input_dir
        self.set_input_base_fname()
        if self.input_base_fname == None:
            exit()
        self.set_output_base_fname()
        if not(os.path.exists(self.output_dir)):
            os.makedirs(self.output_dir)
        self.delete = parsed.delete
    
    def _setup_logger(self):
        self.log_path = os.path.join(
            self.output_dir, 
            self.output_base_fname + '.aggregator' + '.' + self.level + '.log')
        self.logger = logging.getLogger('mapper_log')
        self.logger.propagate = False
        self.logger.setLevel('INFO')
        handler = logging.FileHandler(self.log_path, mode='w')
        formatter = logging.Formatter()
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.info('Aggregator log')
        self.logger.info('Opened %s' %time.asctime())
        self.logger.info('Input directory: %s' %self.input_dir)
        
    def _read_opts(self):
        opt_file = open(os.path.join(os.path.dirname(__file__),
                                     'aggregator.yml'))
        self.opts = yaml.load(opt_file)
        opt_file.close()        

    def run(self):
        self._setup()
        if self.input_base_fname == None:
            return
        start_time = time.time()
        self.logger.info('Begin run(): %s' %\
                         time.asctime(time.localtime(start_time)))
        self.dbconn.commit()
        self.cursor.execute('pragma synchronous=0;')
        self.cursor.execute('pragma journal_mode=WAL;')
        n = 0
        for _, rd in self.base_reader.loop_data():
            n += 1
            names = list(rd.keys())
            values = []
            for name in names:
                val = rd[name]
                valtype = type(val)
                if valtype is str:
                    val = '\'' + val + '\''
                else:
                    if val == None:
                        val = '\'\''
                    else:
                        val = str(val)
                values.append(val)
            q = 'insert into %s (%s) values (%s);' \
                %(self.table_name, 
                  ', '.join([self.base_prefix + '__' + v for v in names]), 
                  ', '.join(values))
            self.cursor.execute(q)
            if n%self.commit_threshold == 0:
                self.dbconn.commit()
        self.dbconn.commit()
        for annot_name in self.annotators:
            reader = self.readers[annot_name]
            n = 0
            for _, rd in reader.loop_data():
                n += 1
                key_val = rd[self.key_name]
                reader_col_names = [x for x in rd if x != self.key_name]
                update_toks = []
                for reader_col_name in reader_col_names:
                    db_col_name = '%s__%s' %(annot_name, reader_col_name)
                    val = rd[reader_col_name]
                    set_val = 'null'
                    if val is not None:
                        if type(val) is str:
                            set_val = '"%s"' %val
                        else:
                            set_val = str(val)
                    update_toks.append('%s=%s' %(db_col_name, set_val))
                q = 'update %s set %s where %s="%s";' %(
                    self.table_name,
                    ', '.join(update_toks),
                    self.base_prefix + '__' + self.key_name,
                    key_val)
                self.cursor.execute(q)
                if n%self.commit_threshold == 0:
                    self.dbconn.commit()
            self.dbconn.commit()
        self.cursor.execute('pragma synchronous=2;')
        self.cursor.execute('pragma journal_mode=delete;')
        end_time = time.time()
        self.logger.info('End run(): %s' %time.asctime(time.localtime(end_time)))
        runtime = end_time - start_time
        self.logger.info('Runtime: %s' %round(runtime, 3))
        self._cleanup()
        
    def _cleanup(self):
        self.cursor.close()
        self.dbconn.close()
        self.logger.info('Finished')
    
    def set_input_base_fname (self):
        crv_fname = self.name + '.crv'
        crx_fname = self.name + '.crx'
        crg_fname = self.name + '.crg'
        crs_fname = self.name + '.crs'
        crm_fname = self.name + '.crm'
        for fname in os.listdir(self.input_dir):
            if self.level == 'variant':
                if fname == crx_fname:
                    self.input_base_fname = fname
                elif fname == crv_fname and not self.input_base_fname:
                    self.input_base_fname = fname
            elif self.level == 'gene' and fname == crg_fname:
                self.input_base_fname = fname
            elif self.level == 'sample' and fname == crs_fname:
                self.input_base_fname = fname
            elif self.level == 'mapping' and fname == crm_fname:
                self.input_base_fname = fname
    
    def set_output_base_fname (self):
        self.output_base_fname = self.name
        
    def _setup(self):
        self.logger.info('Begin setup')
        if self.level == 'variant':
            self.key_name = 'uid'
        elif self.level == 'gene':
            self.key_name = 'hugo'
        elif self.level == 'sample':
            self.key_name = ''
        elif self.level == 'mapping':
            self.key_name = ''
        self.table_name = self.level
        self.header_table_name = self.table_name + '_header'
        annot_name_re = re.compile('.*\.(.*)\.[var,gen]')
        for fname in os.listdir(self.input_dir):
            if fname.startswith(self.name + '.'):
                if self.level == 'variant' and fname.endswith('.var'):
                    annot_name_match = annot_name_re.match(fname)
                    annot_name = annot_name_match.group(1)
                    self.annotators.append(annot_name)
                    self.ipaths[annot_name] = \
                        os.path.join(self.input_dir, fname)
                elif self.level == 'gene' and fname.endswith('.gen'):
                    annot_name_match = annot_name_re.match(fname)
                    annot_name = annot_name_match.group(1)
                    self.annotators.append(annot_name)
                    self.ipaths[annot_name] = \
                        os.path.join(self.input_dir, fname)
        self.annotators.sort()
        self.base_fpath = os.path.join(self.input_dir, self.input_base_fname)
        self._setup_io()
        self._setup_table()
  
    def _setup_table(self):
        columns = []
        unique_names = set([])
        
        # annotator table
        annotator_table = self.level + '_annotator'
        q = 'drop table if exists {:}'.format(annotator_table)
        self.cursor.execute(q)
        q = 'create table {:} (name text, displayname text)'.format(
            annotator_table)
        self.cursor.execute(q)
        q = 'insert into {:} values ("{:}", "{:}")'.format(
            annotator_table, 'base', 'Base Information')
        self.cursor.execute(q)
        for _, col_def in self.base_reader.get_all_col_defs().items():
            col_name = self.base_prefix + '__' + col_def['name']
            columns.append([col_name, col_def['type'], col_def['title']])
            unique_names.add(col_name)
        for annot_name in self.annotators:
            reader = self.readers[annot_name]
            annotator_name = reader.get_annotator_name()
            if annotator_name == '':
                annotator_name = annot_name
            annotator_displayname = reader.get_annotator_displayname()
            if annotator_displayname == '':
                annotator_displayname = annotator_name.upper()
            q = 'insert into {:} values ("{:}", "{:}")'.format(
                annotator_table, annotator_name, annotator_displayname)
            self.cursor.execute(q)
            orded_col_index = sorted(list(reader.get_all_col_defs().keys()))
            for col_index in orded_col_index:
                col_def = reader.get_col_def(col_index)
                reader_col_name = col_def['name']
                if reader_col_name == self.key_name: continue
                db_col_name = '%s__%s' %(annot_name, reader_col_name)
                db_type = col_def['type']
                db_col_title = col_def['title']
                if db_col_name in unique_names:
                    err_msg = 'Duplicate column name %s found in %s. ' \
                        %(db_col_name, reader.path)
                    sys.exit(err_msg)
                else:
                    columns.append([db_col_name, db_type, db_col_title])
                    unique_names.add(db_col_name)
                    
        col_def_strings = []
        for col in columns:
            name = col[0]
            sql_type = self.cr_type_to_sql[col[1]]
            s = name + ' ' + sql_type
            col_def_strings.append(s)
            
        # data table
        q = 'drop table if exists %s' %self.table_name
        self.cursor.execute(q)
        q = 'create table %s (%s);' \
            %(self.table_name, ', '.join(col_def_strings))
        self.cursor.execute(q)
        
        # index tables
        index_n = 0
        # index_columns is a list of columns to include in this index
        for index_columns in self.base_reader.get_index_columns():
            cols = ['base__{0}'.format(x) for x in index_columns]
            q = 'create index {table_name}_idx_{idx_num} on {table_name} ({columns});'\
                .format(table_name = self.table_name,
                        idx_num = str(index_n),
                        columns = ', '.join(cols)
                        )
            self.cursor.execute(q)
            index_n += 1
        
        # header table
        q = 'drop table if exists %s' %self.header_table_name
        self.cursor.execute(q)
        q = 'create table %s (col_name text, col_title text, col_type text);' \
            %(self.header_table_name)
        self.cursor.execute(q)
        for col_name, col_type, col_title in columns:
            q = 'insert into {} values ("{}", "{}", "{}")'.format(
                self.header_table_name, 
                col_name, 
                col_title, 
                col_type)
            self.cursor.execute(q)
            
    def _setup_io(self):
        self.base_reader = CravatReader(self.base_fpath)
        for annot_name in self.annotators:
            self.readers[annot_name] = CravatReader(self.ipaths[annot_name])
        self.db_fname = self.output_base_fname + '.sqlite'
        self.db_path = os.path.join(self.output_dir, self.db_fname)
        if self.delete and os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.dbconn = sqlite3.connect(self.db_path)
        self.cursor = self.dbconn.cursor()
        
                
if __name__ == '__main__':
    aggregator = Aggregator(sys.argv)
    aggregator.run()