# Distributed under the MIT software license, see the accompanying
# file LICENSE or http://www.opensource.org/licenses/mit-license.php.

import cPickle as pickle
import os
from operator import itemgetter

from qrlcore import db, logger
import configuration as config
from qrlcore.merkle import sha256


# state functions
# first iteration - state data stored in leveldb file
# state holds address balances, the transaction nonce and a list of pubhash keys used for each tx - to prevent key reuse.

class State:
    # TODO: A more appropriate name would be something like persistent state
    def __init__(self):
        self.db = db.DB()  # generate db object here

    def stake_list_get(self):
        try:
            return self.db.get('stake_list')
        except Exception as e:
            logger.warn("stake_list_get: %s %s", type(e), e.message)
            return []

    def stake_list_put(self, sl):
        try:
            self.db.put('stake_list', sl)
        except Exception as e:
            logger.warn("stake_list_put: %s %s", type(e), e.message)
            return False

    def next_stake_list_get(self):
        try:
            return self.db.get('next_stake_list')
        except Exception as e:
            logger.warn("next_stake_list_get: %s %s", type(e), e.message)
            return []

    def next_stake_list_put(self, next_sl):
        try:
            self.db.put('next_stake_list', next_sl)
        except Exception as e:
            logger.warn("next_stake_list_put: %s %s", type(e), e.message)
            return False

    def put_epoch_seed(self, epoch_seed):
        try:
            self.db.put('epoch_seed', epoch_seed)
        except Exception as e:
            logger.warn("put_epoch_seed: %s %s", type(e), e.message)
            return False

    def get_epoch_seed(self):
        try:
            return self.db.get('epoch_seed')
        except Exception as e:
            logger.warn("get_epoch_seed: %s %s", type(e), e.message)
            return False

    def state_uptodate(self, height):  # check state db marker to current blockheight.
        if height == self.db.get('blockheight'):
            return True
        return False

    def state_blockheight(self):
        return self.db.get('blockheight')

    def state_get_txn_count(self, addr):
        try:
            return self.db.get('txn_count_' + addr)
        except Exception as e:
            logger.warn("state_get_txn_count: %s %s", type(e), e.message)
            return 0

    def state_get_address(self, addr):
        try:
            return self.db.get(addr)
        except Exception as e:
            logger.warn("state_get_address: %s %s", type(e), e.message)
            return [0, 0, []]

    def state_address_used(self, addr):  # if excepts then address does not exist..
        try:
            return self.db.get(addr)
        except Exception as e:
            logger.warn("state_address_used: %s %s", type(e), e.message)
            return False

    def state_balance(self, addr):
        try:
            return self.db.get(addr)[1]
        except Exception as e:
            logger.warn("state_balance: %s %s", type(e), e.message)
            return 0

    def state_nonce(self, addr):
        try:
            return self.db.get(addr)[0]
        except Exception as e:
            logger.warn("state_nonce: %s %s", type(e), e.message)
            return 0

    def state_pubhash(self, addr):
        try:
            return self.db.get(addr)[2]
        except Exception as e:
            logger.warn("state_pubhash: %s %s", type(e), e.message)
            return []

    def state_hrs(self, hrs):
        try:
            return self.db.get('hrs' + hrs)
        except Exception as e:
            logger.warn("state_hrs: %s %s", type(e), e.message)
            return False

    def state_validate_tx_pool(self, chain):
        result = True

        for tx in chain.transaction_pool:
            if tx.state_validate_tx(state=self) is False:
                result = False
                logger.info(('tx', tx.txhash, 'failed..'))
                chain.remove_tx_from_pool(tx)

        return result

    def state_add_block(self, chain, block):
        address_txn = {block.blockheader.stake_selector: self.state_get_address(block.blockheader.stake_selector)}

        for st in block.stake:
            if st.txfrom not in address_txn:
                address_txn[st.txfrom] = self.state_get_address(st.txfrom)

        for tx in block.transactions:
            if tx.txfrom not in address_txn:
                address_txn[tx.txfrom] = self.state_get_address(tx.txfrom)
            if tx.txto not in address_txn:
                address_txn[tx.txto] = self.state_get_address(tx.txto)

        # reminder contents: (state address -> nonce, balance, [pubhash]) (stake -> address, hash_term, nonce)

        next_sl = self.next_stake_list_get()
        sl = self.stake_list_get()

        blocks_left = block.blockheader.blocknumber - (block.blockheader.epoch * config.dev.blocks_per_epoch)
        blocks_left = config.dev.blocks_per_epoch - blocks_left

        if block.blockheader.blocknumber == 1:

            for st in block.stake:
                # update txfrom, hash and stake_nonce against genesis for current or next stake_list
                if st.txfrom == block.blockheader.stake_selector:
                    if st.txfrom in chain.m_blockchain[0].stake_list:
                        sl.append([st.txfrom, st.hash, 1, st.first_hash, st.balance])
                    else:
                        logger.info(('designated staker not in genesis..'))
                        return False
                else:
                    if st.txfrom in chain.m_blockchain[0].stake_list:
                        sl.append([st.txfrom, st.hash, 0, st.first_hash, st.balance])
                    else:
                        next_sl.append([st.txfrom, st.hash, 0, st.first_hash, st.balance])

                pub = st.pub
                pub = [''.join(pub[0][0]), pub[0][1], ''.join(pub[2:])]
                pubhash = sha256(''.join(pub))
                address_txn[st.txfrom][2].append(pubhash)

                logger.info(('state st.txfrom', self.state_get_address(st.txfrom)))

            epoch_seed = self.calc_seed(sl)
            chain.block_chain_buffer.epoch_seed = epoch_seed
            self.put_epoch_seed(epoch_seed)

            stake_list = sorted(sl, key=lambda staker: chain.score(stake_address=staker[0],
                                                                   reveal_one=sha256(str(staker[1])),
                                                                   balance=self.state_balance(staker[0]),
                                                                   seed=epoch_seed))
            '''
            chain.epoch_PRF = merkle.GEN_range(
                chain.m_blockchain[block.blockheader.epoch * config.dev.blocks_per_epoch].stake_seed,
                1,
                config.dev.blocks_per_epoch,
                32)
            chain.block_chain_buffer.epoch_PRF[0] = chain.epoch_PRF
            chain.epoch_prf = chain.pos_block_selector(
                chain.m_blockchain[block.blockheader.epoch * config.dev.blocks_per_epoch].stake_seed,
                len(stake_list))
            '''

            # if stake_list[
            #    chain.epoch_prf[block.blockheader.blocknumber - block.blockheader.epoch * config.dev.blocks_per_epoch]][
            #    0] != block.blockheader.stake_selector:
            if stake_list[0][0] != block.blockheader.stake_selector:
                logger.info('stake selector wrong..')
                return

            chain.my[0][1].hashchain(epoch=0)
            chain.hash_chain = chain.my[0][1].hc
            chain.wallet.f_save_wallet()

        else:

            found = False

            # increase the stake_nonce of state selector..must be in stake list..
            logger.info(
                ('BLOCK:', block.blockheader.blocknumber, 'stake nonce:', block.blockheader.stake_nonce, 'epoch: ',
                 block.blockheader.epoch, 'blocks_left: ', blocks_left - 1, 'stake_selector: ',
                 block.blockheader.stake_selector))

            for s in sl:
                if block.blockheader.stake_selector == s[0]:
                    found = True
                    s[2] += 1
                    if s[2] != block.blockheader.stake_nonce:
                        logger.info('stake_nonce wrong..')
                        logger.info(('block STake Selector ', block.blockheader.stake_selector))
                        logger.info(('Expected Nonce ', str(s[2])))
                        logger.info(('Actual Nonce ', str(block.blockheader.stake_nonce)))
                        return
                    break

            if not found:
                logger.info('stake selector not in stake_list_get')
                return

            # update and re-order the next_stake_list:

            for st in block.stake:
                pub = st.pub
                pub = [''.join(pub[0][0]), pub[0][1], ''.join(pub[2:])]
                pubhash = sha256(''.join(pub))
                found = False

                for s in next_sl:
                    # already in the next stake list, ignore for staker list but update as usual the state_for_address..
                    if st.txfrom == s[0]:
                        found = True
                        if s[3] is None and st.first_hash is not None:
                            threshold_block = self.get_staker_threshold_blocknum(next_sl, s[0])
                            epoch_blocknum = config.dev.blocks_per_epoch - blocks_left
                            # TODO: Make sure the block doesn't add such ST transaction
                            # above has to be implemented into st.validate
                            if epoch_blocknum >= threshold_block - 1:
                                s[3] = st.first_hash
                            else:
                                logger.info(('^^^^^^Rejected as ', epoch_blocknum, threshold_block - 1))
                                logger.info(('Loss of data ', s[0], 'old ', s[3], 'new ', st.first_hash))
                        # else:
                        #    logger.info(('Else of next_sl ', s[0], s[3], st.first_hash ))
                        break

                address_txn[st.txfrom][2].append(pubhash)

                if not found:
                    next_sl.append([st.txfrom, st.hash, 0, st.first_hash, st.balance])

        # cycle through every tx in the new block to check state

        for tx in block.transactions:

            pub = tx.pub
            if tx.type == 'TX':
                pub = [''.join(pub[0][0]), pub[0][1], ''.join(pub[2:])]

            pubhash = sha256(''.join(pub))

            # basic tx state checks..

            # if s1[1] - tx.amount < 0:
            if address_txn[tx.txfrom][1] - tx.amount < 0:
                logger.info((tx, tx.txfrom, 'exceeds balance, invalid tx'))
                return False

            if tx.nonce != address_txn[tx.txfrom][0] + 1:
                logger.info('nonce incorrect, invalid tx')
                logger.info((tx, tx.txfrom, tx.nonce))
                return False

            if pubhash in address_txn[tx.txfrom][2]:
                logger.info(('pubkey reuse detected: invalid tx', tx.txhash))
                return False

            # add a check to prevent spend from stake address..
            # if tx.txfrom in stake_list_get():
            # logger.info(( 'attempt to spend from a stake address: invalid tx type'
            # break

            address_txn[tx.txfrom][0] += 1
            address_txn[tx.txfrom][1] -= tx.amount
            address_txn[tx.txfrom][2].append(pubhash)

            address_txn[tx.txto][1] = address_txn[tx.txto][1] + tx.amount

        # committing state

        # first the coinbase address is updated
        address_txn[block.blockheader.stake_selector][1] += block.blockheader.block_reward

        for address in address_txn:
            self.db.put(address, address_txn[address])

        if block.blockheader.blocknumber > 1 or block.blockheader.blocknumber == 1:
            self.stake_list_put(sl)
            self.next_stake_list_put(sorted(next_sl, key=itemgetter(1)))

        if blocks_left == 1:
            logger.info((
                'EPOCH change: resetting stake_list, activating next_stake_list, updating PRF with seed+entropy, '
                'updating wallet hashchains..'))

            sl = next_sl
            sl = filter(lambda staker: staker[3] is not None, sl)

            # epoch_seed = self.calc_seed(sl)
            # epoch = (block.blockheader.blocknumber // config.dev.blocks_per_epoch)
            # chain.block_chain_buffer.epoch_seed = epoch_seed
            # chain.block_chain_buffer.epoch_seed[epoch + 1] = epoch_seed
            # self.put_epoch_seed(epoch_seed)
            # TODO: unlock stakers fund who are not selected for the epoch
            self.stake_list_put(sl)
            del next_sl[:]
            self.next_stake_list_put(next_sl)

            chain.my[0][1].hashchain(epoch=block.blockheader.epoch + 1)  ####
            chain.hash_chain = chain.my[0][1].hc  ####
            chain.wallet.f_save_wallet()  ####

        self.db.put('blockheight', chain.height() + 1)
        logger.info((block.blockheader.headerhash, str(len(block.transactions)), 'tx ', ' passed verification.'))
        return True

    def calc_seed(self, sl, verbose=False):
        if verbose:
            logger.info(('stake_list --> '))
            for s in sl:
                logger.info((s[0], s[3]))

        epoch_seed = 0

        for staker in sl:
            epoch_seed |= int(str(staker[3]), 16)

        return epoch_seed

    def get_staker_threshold_blocknum(self, next_stake_list, staker_address):
        tmp_next_stake_list = sorted(next_stake_list, key=itemgetter(4))
        total_stakers = len(next_stake_list)
        found_position = -1

        for i in range(total_stakers):
            if tmp_next_stake_list[i] == staker_address:
                found_position = i
                break

        if found_position < total_stakers // 2:
            return config.dev.low_staker_first_hash_block

        return config.dev.high_staker_first_hash_block

    def state_read_genesis(self, genesis_block):
        logger.info(('genesis:'))

        for address in genesis_block.state:
            self.db.put(address[0], address[1])
        return True

    def state_read_chain(self, chain):

        self.db.zero_all_addresses()
        c = chain.m_get_block(0).state
        for address in c:
            self.db.put(address[0], address[1])

        c = chain.m_read_chain()[2:]
        for block in c:

            # update coinbase address state
            stake_selector = self.state_get_address(block.blockheader.stake_selector)
            stake_selector[1] += block.blockheader.block_reward
            self.db.put(block.blockheader.stake_selector, stake_selector)

            for tx in block.transactions:
                pub = tx.pub
                if tx.type == 'TX':
                    pub = [''.join(pub[0][0]), pub[0][1], ''.join(pub[2:])]

                pubhash = sha256(''.join(pub))

                s1 = self.state_get_address(tx.txfrom)

                if s1[1] - tx.amount < 0:
                    logger.info((tx, tx.txfrom, 'exceeds balance, invalid tx', tx.txhash))
                    logger.info((block.blockheader.headerhash, 'failed state checks'))
                    return False

                if tx.nonce != s1[0] + 1:
                    logger.info(('nonce incorrect, invalid tx', tx.txhash))
                    logger.info((block.blockheader.headerhash, 'failed state checks'))
                    return False

                if pubhash in s1[2]:
                    logger.info(('public key re-use detected, invalid tx', tx.txhash))
                    logger.info((block.blockheader.headerhash, 'failed state checks'))
                    return False

                s1[0] += 1
                s1[1] = s1[1] - tx.amount
                s1[2].append(pubhash)
                self.db.put(tx.txfrom, s1)  # must be ordered in case tx.txfrom = tx.txto

                s2 = self.state_get_address(tx.txto)
                s2[1] = s2[1] + tx.amount

                self.db.put(tx.txto, s2)

            logger.info((block, str(len(block.transactions)), 'tx ', ' passed'))

        self.db.put('blockheight', chain.m_blockheight())
        return True
