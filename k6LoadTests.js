/* eslint-disable */
import { sleep } from 'k6';
import http from 'k6/http';

export const options = {
  duration: '10m',
  vus: 50,
  //  vus: 1,
  thresholds: {
    //avg is around ?200ms? on https://venepaikka-api.test.kuva.hel.ninja
    http_req_duration: ['p(95)<5000'],
  },
};

export default () => {
  const url = 'https://venepaikka-api.test.kuva.hel.ninja/graphql';
  const data = 'query=query        { \
            harbors { \
                edges { \
                    node { \
                        geometry { \
                            type \
                            coordinates \
                        } \
                        properties { \
                            name \
                            zipCode \
                            maxWidth \
                            maxLength \
                            maxDepth \
                            numberOfPlaces \
                            numberOfFreePlaces \
                            numberOfInactivePlaces \
                            createdAt \
                            modifiedAt \
                        } \
                    } \
                } \
            } \
        }';
  const res = http.post(url, data, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });

  //10 loads per minute
  sleep(6);
};
